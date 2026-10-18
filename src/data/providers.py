"""Data providers behind one interface.

No single vendor covers forex + futures + metals cheaply, so the repo talks to
several and the registry picks one per instrument. Swapping vendor later means
writing one class, not touching the strategy code.

Every provider returns the same thing: a list of Bar, oldest first, UTC.

    from src.data.providers import get_bars
    bars = get_bars("EURUSD", "5m", count=5000)

Credentials come from environment variables. Nothing is hard-coded and no key
is ever written into a committed file.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..core.types import Bar, Timeframe


class ProviderError(RuntimeError):
    """Raised with the vendor's own message. Never swallowed — a silent data
    failure produces a confident, wrong backtest."""


# --------------------------------------------------------------- base

class Provider(ABC):
    name: str = "base"
    #: canonical symbol -> this vendor's symbol
    symbols: Dict[str, str] = {}
    #: our Timeframe -> this vendor's granularity string
    granularity: Dict[str, str] = {}

    #: most vendors cap one response; paging walks backwards in chunks
    max_per_request: int = 5000
    supports_paging: bool = False
    #: fast enough to serve an interactive chart
    live_capable: bool = True

    @abstractmethod
    def fetch(self, symbol: str, tf: str, count: int,
              before_ts: Optional[int] = None,
              deadline: Optional[float] = None) -> List[Bar]:
        ...

    def supports(self, symbol: str, tf: str) -> bool:
        return symbol in self.symbols and tf in self.granularity

    # -- helpers ---------------------------------------------------

    @staticmethod
    def _get_json(url: str, headers: Optional[dict] = None, timeout: int = 30) -> dict:
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:400]
            raise ProviderError(f"HTTP {e.code} from {url.split('?')[0]}: {body}") from e
        except Exception as e:
            raise ProviderError(f"{type(e).__name__} calling {url.split('?')[0]}: {e}") from e

    @staticmethod
    def _env(key: str, why: str) -> str:
        v = os.environ.get(key)
        if not v:
            raise ProviderError(f"{key} is not set. {why}")
        return v

    @staticmethod
    def _sane(bars: List[Bar], symbol: str) -> List[Bar]:
        """Reject malformed bars loudly rather than charting nonsense."""
        bad = [b for b in bars
               if not (b.low <= b.open <= b.high and b.low <= b.close <= b.high)]
        if bad:
            raise ProviderError(
                f"{symbol}: {len(bad)} bars have OHLC outside their own high/low")
        bars.sort(key=lambda b: b.ts)
        return bars


# --------------------------------------------------------------- OANDA

class Oanda(Provider):
    """Forex and metal CFDs. A practice account is free and gives the same
    market data as a live one, so this is the cheapest real feed for EURUSD
    and gold.

        OANDA_TOKEN=...            (Account -> Manage API Access)
        OANDA_ENV=practice|live    (default practice)

    Max 5000 candles per request — `count` above that is capped, not paged.
    """
    name = "oanda"
    max_per_request = 5000
    supports_paging = True
    HOSTS = {
        "practice": "https://api-fxpractice.oanda.com",
        "live": "https://api-fxtrade.oanda.com",
    }
    symbols = {
        "EURUSD": "EUR_USD", "GBPUSD": "GBP_USD", "USDJPY": "USD_JPY",
        "AUDUSD": "AUD_USD", "USDCAD": "USD_CAD", "USDCHF": "USD_CHF",
        "NZDUSD": "NZD_USD", "EURGBP": "EUR_GBP", "EURJPY": "EUR_JPY",
        "GBPJPY": "GBP_JPY",
        # Metals trade as CFDs here. Confirm they're in your account's
        # instrument list — availability varies by region and regulator.
        "XAUUSD": "XAU_USD", "XAGUSD": "XAG_USD",
        # Index CFDs. NOT the CME futures contract — different product,
        # different hours, different settlement. Fine for structure, wrong
        # if you need the actual NQ/ES contract.
        "NAS100": "NAS100_USD", "SPX500": "SPX500_USD", "US30": "US30_USD",
        # Dollar index proxy.
        "USDX": "USB10Y_USD",
    }
    granularity = {
        "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
        "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
    }

    def fetch(self, symbol: str, tf: str, count: int = 5000,
              before_ts: Optional[int] = None, deadline: Optional[float] = None) -> List[Bar]:
        token = self._env("OANDA_TOKEN",
                          "Create a free practice account and generate a token.")
        host = self.HOSTS[os.environ.get("OANDA_ENV", "practice")]
        inst = self.symbols[symbol]

        params = {
            "granularity": self.granularity[tf],
            "count": min(count, 5000),
            "price": "M",          # midpoint
        }
        # Paging: ask for the `count` candles ending just before this time.
        if before_ts:
            params["to"] = str(before_ts)
        q = urllib.parse.urlencode(params)
        data = self._get_json(
            f"{host}/v3/instruments/{inst}/candles?{q}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept-Datetime-Format": "UNIX"},
        )

        bars = []
        for c in data.get("candles", []):
            if not c.get("complete"):
                continue           # a forming bar is not a closed bar
            m = c["mid"]
            bars.append(Bar(
                ts=int(float(c["time"])),
                open=float(m["o"]), high=float(m["h"]),
                low=float(m["l"]), close=float(m["c"]),
                volume=float(c.get("volume", 0)),
            ))
        return self._sane(bars, symbol)


# --------------------------------------------------------------- Twelve Data

class TwelveData(Provider):
    """Forex, indices, some commodities. Free tier is small but real.

        TWELVEDATA_KEY=...

    Check your plan's current request allowance — the free limits move.
    """
    name = "twelvedata"
    #: `time_series` takes end_date, which is all paging backwards needs.
    #: Without this the only pager left was Dukascopy, and get_history chose
    #: it for a request-time fetch — one HTTP call per hour of history, so a
    #: six-page pull meant 50,000 requests and a guaranteed 504.
    supports_paging = True
    max_per_request = 5000
    symbols = {
        "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
        "AUDUSD": "AUD/USD", "USDCAD": "USD/CAD", "USDCHF": "USD/CHF",
        "NZDUSD": "NZD/USD", "EURGBP": "EUR/GBP",
        # These were missing, so EURJPY fell through to a slow provider and
        # the pair selector looked broken.
        "EURJPY": "EUR/JPY", "GBPJPY": "GBP/JPY",
        "XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD",
    }
    granularity = {
        "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "1h", "4h": "4h", "1d": "1day", "1w": "1week",
    }

    def fetch(self, symbol: str, tf: str, count: int = 5000,
              before_ts: Optional[int] = None, deadline: Optional[float] = None) -> List[Bar]:
        key = self._env("TWELVEDATA_KEY", "Sign up at twelvedata.com for a free key.")
        params = {
            "symbol": self.symbols[symbol],
            "interval": self.granularity[tf],
            "outputsize": min(count, self.max_per_request),
            "format": "JSON",
            # Be explicit rather than lucky. Without this the vendor answers in
            # the exchange's timezone and the parser below assumes UTC for any
            # naive string — which is only correct by accident.
            "timezone": "UTC",
            "apikey": key,
        }
        if before_ts:
            # end_date is inclusive, so step back one second or every page
            # would hand back the cursor bar again.
            params["end_date"] = datetime.fromtimestamp(
                before_ts - 1, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        q = urllib.parse.urlencode(params)
        data = self._get_json(f"https://api.twelvedata.com/time_series?{q}")

        if data.get("status") == "error":
            raise ProviderError(f"twelvedata: {data.get('message')}")

        bars = []
        for v in data.get("values", []):
            dt = datetime.fromisoformat(v["datetime"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            bars.append(Bar(
                ts=int(dt.timestamp()),
                open=float(v["open"]), high=float(v["high"]),
                low=float(v["low"]), close=float(v["close"]),
                volume=float(v.get("volume") or 0),
            ))
        return self._sane(bars, symbol)


# --------------------------------------------------------------- Yahoo

class Yahoo(Provider):
    """Free, no key, and the only no-cost source here that carries the actual
    CME futures contracts (NQ=F, ES=F, GC=F).

    Caveats you should know before trusting it:
      - It is an undocumented endpoint, not a supported API. It can change or
        start refusing requests without notice.
      - Intraday history is shallow; roughly 60 days at 5m, less at 1m.
      - Continuous contracts (=F) splice contract months together. The splice
        creates price jumps that are NOT market moves — your FVG and order
        block detectors will happily find levels inside a roll gap.

    Fine for personal research. Do not build a product on it.
    """
    name = "yahoo"
    symbols = {
        "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
        "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X",
        "XAUUSD": "GC=F",      # gold futures, not spot
        "NQ": "NQ=F", "ES": "ES=F", "YM": "YM=F", "RTY": "RTY=F",
        "GOLD": "GC=F", "SILVER": "SI=F", "OIL": "CL=F",
        "DXY": "DX-Y.NYB",
    }
    granularity = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "60m", "1d": "1d", "1w": "1wk",
    }
    #: how much history the endpoint will actually return per granularity
    RANGE = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
             "60m": "730d", "1d": "max", "1wk": "max"}

    def fetch(self, symbol: str, tf: str, count: int = 5000,
              before_ts: Optional[int] = None, deadline: Optional[float] = None) -> List[Bar]:
        # Yahoo's range parameter has no cursor, so paging is not available.
        sym = urllib.parse.quote(self.symbols[symbol])
        g = self.granularity[tf]
        q = urllib.parse.urlencode({"interval": g, "range": self.RANGE[g]})
        data = self._get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?{q}",
            headers={"User-Agent": "Mozilla/5.0"},
        )

        res = (data.get("chart") or {}).get("result")
        if not res:
            err = (data.get("chart") or {}).get("error")
            raise ProviderError(f"yahoo returned no data for {symbol}: {err}")

        r = res[0]
        ts = r.get("timestamp") or []
        q0 = r["indicators"]["quote"][0]

        bars = []
        for i, t in enumerate(ts):
            o, h, l, c = q0["open"][i], q0["high"][i], q0["low"][i], q0["close"][i]
            if None in (o, h, l, c):
                continue           # Yahoo pads gaps with nulls
            bars.append(Bar(ts=int(t), open=float(o), high=float(h),
                            low=float(l), close=float(c),
                            volume=float((q0.get("volume") or [0])[i] or 0)))
        return self._sane(bars, symbol)[-count:]


# --------------------------------------------------------------- Alpha Vantage

class AlphaVantage(Provider):
    """Forex intraday. Free key, signup open worldwide — it is a data vendor,
    not a broker, so there is no per-country licensing.

        ALPHAVANTAGE_KEY=...        (alphavantage.co/support/#api-key)

    The free allowance is small (documented as 25 requests/day at the time of
    writing — check the current figure). Fine as a fallback, not as a primary.
    No metals on the free tier.
    """
    name = "alphavantage"
    live_capable = True
    symbols = {
        "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
        "USDJPY": ("USD", "JPY"), "AUDUSD": ("AUD", "USD"),
        "USDCAD": ("USD", "CAD"), "USDCHF": ("USD", "CHF"),
        "NZDUSD": ("NZD", "USD"), "EURGBP": ("EUR", "GBP"),
        "EURJPY": ("EUR", "JPY"), "GBPJPY": ("GBP", "JPY"),
    }
    granularity = {"1m": "1min", "5m": "5min", "15m": "15min",
                   "30m": "30min", "1h": "60min"}

    def fetch(self, symbol: str, tf: str, count: int = 5000,
              before_ts: Optional[int] = None, deadline: Optional[float] = None) -> List[Bar]:
        key = self._env("ALPHAVANTAGE_KEY",
                        "Free key at alphavantage.co/support/#api-key")
        frm, to = self.symbols[symbol]
        q = urllib.parse.urlencode({
            "function": "FX_INTRADAY", "from_symbol": frm, "to_symbol": to,
            "interval": self.granularity[tf], "outputsize": "full",
            "apikey": key,
        })
        data = self._get_json(f"https://www.alphavantage.co/query?{q}")

        # Alpha Vantage returns 200 with an explanatory body when throttled.
        for bad in ("Note", "Information", "Error Message"):
            if bad in data:
                raise ProviderError(f"alphavantage: {data[bad]}")

        series = next((v for k, v in data.items() if "Time Series" in k), None)
        if not series:
            raise ProviderError(f"alphavantage: unexpected response for {symbol}")

        bars = []
        for stamp, v in series.items():
            dt = datetime.fromisoformat(stamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            bars.append(Bar(
                ts=int(dt.timestamp()),
                open=float(v["1. open"]), high=float(v["2. high"]),
                low=float(v["3. low"]), close=float(v["4. close"]),
            ))
        return self._sane(bars, symbol)[-count:]


# --------------------------------------------------------------- Dukascopy

class Dukascopy(Provider):
    """Public historical datafeed. **No account, no key, no country check.**

    This is the answer when a broker refuses to open an account in your
    country: Dukascopy's historical files are a static server, so geography
    never enters into it. It also has by far the deepest free history, which
    is what the backtest needs.

    Slow — one request per hour of history — so it is ordered last for live
    dashboard use but is the right choice for `get_history`.

    Verify the decode once before trusting it:
        python3 -m src.data.dukascopy --verify EURUSD
    """
    name = "dukascopy"
    supports_paging = True
    max_per_request = 100000
    #: Far too slow for a live chart — one request per hour of history.
    #: get_bars only falls back to it when nothing else carries the symbol.
    live_capable = False

    def __init__(self):
        from .dukascopy import SYMBOLS, TF_SECONDS
        self.symbols = {k: k for k in SYMBOLS}
        self.granularity = {k: k for k in TF_SECONDS}

    def fetch(self, symbol: str, tf: str, count: int = 5000,
              before_ts: Optional[int] = None, deadline: Optional[float] = None) -> List[Bar]:
        from datetime import datetime as _dt
        from .dukascopy import DukascopyError, TF_SECONDS
        from .dukascopy import fetch as duka_fetch

        # Work backwards from count: how many days of history is that?
        secs = TF_SECONDS[tf]
        days = max(2, int((count * secs) / 86400 * 1.6) + 1)   # weekends cost ~30%
        days = min(days, 120)                                   # keep it sane

        end = (_dt.fromtimestamp(before_ts, tz=timezone.utc)
               if before_ts else None)
        try:
            return duka_fetch(symbol, tf, days=days, end=end, quiet=True,
                              deadline=deadline)[-count:]
        except DukascopyError as e:
            raise ProviderError(f"dukascopy: {e}") from e


# --------------------------------------------------------------- registry

#: Order matters — first provider that supports the symbol and has its key wins.
#:
#: OANDA first where it works, but it is a BROKER and refuses accounts in many
#: countries (Uganda among them). Everything after it is a data vendor or a
#: public file server, none of which care where you live. Dukascopy is last
#: for live use because it is slow, but it needs no account at all and has the
#: deepest history — see docs/DATA.md.
PROVIDERS: List[Provider] = [
    Oanda(), TwelveData(), AlphaVantage(), Yahoo(), Dukascopy(),
]

BY_NAME = {p.name: p for p in PROVIDERS}


def available(symbol: str, tf: str) -> List[str]:
    return [p.name for p in PROVIDERS if p.supports(symbol, tf)]


def is_configured(p: "Provider") -> bool:
    """Does this provider have what it needs to actually answer?

    Keyless sources are always configured; keyed ones need their variable set.
    """
    needs = {"oanda": "OANDA_TOKEN",
             "twelvedata": "TWELVEDATA_KEY",
             "alphavantage": "ALPHAVANTAGE_KEY"}
    key = needs.get(p.name)
    return True if key is None else bool(os.environ.get(key))


def catalogue() -> Dict[str, dict]:
    """Every instrument, who serves it, and whether it is reachable NOW.

    The picker used to list the union of every provider's symbols, including
    ones whose keys are not set. Picking one of those looked like a broken
    selector when it was really a missing key, so reachability is reported
    per instrument rather than left for the user to discover by clicking.
    """
    out: Dict[str, dict] = {}
    for p in PROVIDERS:
        ready = is_configured(p)
        for s in p.symbols:
            e = out.setdefault(s, {"providers": [], "live": [], "history": []})
            e["providers"].append(p.name)
            if not ready:
                continue
            (e["live"] if p.live_capable else e["history"]).append(p.name)

    for s, e in out.items():
        e["reachable"] = bool(e["live"] or e["history"])
        e["live_now"] = bool(e["live"])
        e["best"] = (e["live"] or e["history"] or e["providers"])[0]
        if not e["reachable"]:
            e["why"] = ("no key set for " + ", ".join(e["providers"]))
        elif not e["live_now"]:
            e["why"] = ("history only via " + ", ".join(e["history"])
                        + " — delayed, not a live quote")
        else:
            e["why"] = ""

    return dict(sorted(out.items()))


def pick_pager(symbol: str, tf: str,
               provider: Optional[str] = None,
               interactive: bool = True) -> Optional[Provider]:
    """Which provider should walk backwards through history.

    This used to be `max(pagers, key=max_per_request)`, which reads as "go as
    deep as you can" and behaves as "always pick Dukascopy", because Dukascopy
    advertises 100,000 bars against everyone else's 5,000. Dukascopy also
    issues one HTTP request per HOUR of history, so a six-page pull is 50,000
    requests. Served from a 60-second function that is not deep history, it is
    a 504 — which is exactly what it was.

    The size of a page was never the right question. The right question is
    whether the provider can answer inside the budget it is being called in.
    So an interactive caller only gets a `live_capable` pager; a deep offline
    pull asks for the slow one by name.
    """
    if provider:
        # Named explicitly — honour it. The CLI wants Dukascopy on purpose.
        p = BY_NAME[provider]
        return p if p.supports_paging else None

    # is_configured matters as much as the rest: OANDA ties with Twelve Data
    # on page size and sorts first, so without this the chooser picked it and
    # the whole backtest died on "OANDA_TOKEN is not set" — a missing key
    # reported as a data failure. get_bars already skips unconfigured
    # providers; this path did not.
    pagers = [p for p in PROVIDERS
              if p.supports(symbol, tf) and p.supports_paging
              and is_configured(p)]
    if interactive:
        pagers = [p for p in pagers if p.live_capable]
    if not pagers:
        return None
    return max(pagers, key=lambda p: p.max_per_request)


def get_history(symbol: str, tf: str = "5m", pages: int = 6,
                provider: Optional[str] = None,
                interactive: bool = True,
                budget_seconds: Optional[float] = None) -> List[Bar]:
    """Walk backwards through several requests for a longer history.

    One request caps at 5000 candles — at 5m that is about 17 days, nowhere
    near enough to produce 30+ out-of-sample trades. Six pages gets roughly
    four months.

    Stops early when a page returns nothing new, so a provider that ignores
    the cursor degrades to a single page rather than looping forever, and
    stops early on `budget_seconds` so a slow page cannot run out the clock
    on the caller.
    """
    symbol = symbol.upper()
    pager = pick_pager(symbol, tf, provider, interactive)

    if pager is None:
        slow = [p for p in PROVIDERS
                if p.supports(symbol, tf) and p.supports_paging]
        if interactive and slow:
            # Say which door is shut and where the other one is, rather than
            # quietly returning one page and calling it a backtest.
            raise ProviderError(
                f"No fast provider can page {symbol} {tf}. "
                f"{', '.join(p.name for p in slow)} could, but needs far too "
                "many requests to finish inside a web request — pull it "
                "offline instead (see docs/DATA.md). A key for a paging "
                "provider such as Twelve Data fixes this."
            )
        # Nothing pages at all — one page is genuinely all there is.
        return get_bars(symbol, tf, provider=provider)

    started = time.time()
    out: List[Bar] = []
    cursor: Optional[int] = None
    seen: set = set()

    for _ in range(max(1, pages)):
        batch = pager.fetch(symbol, tf, pager.max_per_request, before_ts=cursor)
        fresh = [b for b in batch if b.ts not in seen]
        if not fresh:
            break
        seen.update(b.ts for b in fresh)
        out = fresh + out
        cursor = min(b.ts for b in fresh)
        if budget_seconds and time.time() - started > budget_seconds:
            break

    out.sort(key=lambda b: b.ts)
    return out


def get_bars(symbol: str, tf: str = "5m", count: int = 5000,
             provider: Optional[str] = None,
             budget_seconds: float = 25.0) -> List[Bar]:
    """Fetch bars, trying each provider that supports the instrument.

    A provider missing its key is skipped, not fatal — the next one gets a go.
    If every provider fails you get all their errors, because 'no data' with no
    reason is the worst possible failure here.
    """
    symbol = symbol.upper()
    if provider:
        chain = [BY_NAME[provider]]
    else:
        supported = [p for p in PROVIDERS if p.supports(symbol, tf)]
        # Try the fast providers first; fall back to slow ones only if no fast
        # provider carries this symbol at all.
        fast = [p for p in supported if p.live_capable]
        chain = fast + [p for p in supported if not p.live_capable]

    if not chain:
        raise ProviderError(
            f"No provider covers {symbol} at {tf}. Known: "
            f"{', '.join(catalogue())}")

    # A time budget for the WHOLE chain. Without it, one unreachable provider
    # retrying with backoff holds the request open and the dashboard looks
    # frozen — which is exactly what happened in testing when a host was
    # blocked: 128 connection attempts and a page that never loaded.
    import time as _time
    deadline = _time.monotonic() + budget_seconds

    errors = []
    for p in chain:
        if not p.supports(symbol, tf):
            errors.append(f"{p.name}: does not carry {symbol} at {tf}")
            continue
        left = deadline - _time.monotonic()
        if left <= 0:
            errors.append(f"{p.name}: skipped, {budget_seconds}s budget spent")
            continue
        try:
            bars = p.fetch(symbol, tf, count, deadline=deadline)
            if bars:
                return bars
            errors.append(f"{p.name}: returned no bars")
        except ProviderError as e:
            errors.append(f"{p.name}: {e}")

    raise ProviderError(f"All providers failed for {symbol} {tf}:\n  "
                        + "\n  ".join(errors))
