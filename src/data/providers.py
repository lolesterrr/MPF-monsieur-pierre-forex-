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

    @abstractmethod
    def fetch(self, symbol: str, tf: str, count: int,
              before_ts: Optional[int] = None) -> List[Bar]:
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
              before_ts: Optional[int] = None) -> List[Bar]:
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
    symbols = {
        "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
        "AUDUSD": "AUD/USD", "USDCAD": "USD/CAD", "USDCHF": "USD/CHF",
        "NZDUSD": "NZD/USD", "EURGBP": "EUR/GBP",
        "XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD",
    }
    granularity = {
        "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "1h", "4h": "4h", "1d": "1day", "1w": "1week",
    }

    def fetch(self, symbol: str, tf: str, count: int = 5000,
              before_ts: Optional[int] = None) -> List[Bar]:
        key = self._env("TWELVEDATA_KEY", "Sign up at twelvedata.com for a free key.")
        q = urllib.parse.urlencode({
            "symbol": self.symbols[symbol],
            "interval": self.granularity[tf],
            "outputsize": min(count, 5000),
            "format": "JSON",
            "apikey": key,
        })
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
              before_ts: Optional[int] = None) -> List[Bar]:
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


# --------------------------------------------------------------- registry

#: Order matters — first provider that supports the symbol and has its key wins.
PROVIDERS: List[Provider] = [Oanda(), TwelveData(), Yahoo()]

BY_NAME = {p.name: p for p in PROVIDERS}


def available(symbol: str, tf: str) -> List[str]:
    return [p.name for p in PROVIDERS if p.supports(symbol, tf)]


def catalogue() -> Dict[str, List[str]]:
    """Every instrument this build can reach, and who serves it."""
    out: Dict[str, List[str]] = {}
    for p in PROVIDERS:
        for s in p.symbols:
            out.setdefault(s, []).append(p.name)
    return dict(sorted(out.items()))


def get_history(symbol: str, tf: str = "5m", pages: int = 6,
                provider: Optional[str] = None) -> List[Bar]:
    """Walk backwards through several requests for a longer history.

    One OANDA request caps at 5000 candles — at 5m that is about 17 days,
    which is nowhere near enough to produce 30+ out-of-sample trades. Six
    pages gets roughly four months.

    Stops early when a page returns nothing new, so a provider that ignores
    the cursor degrades to a single page rather than looping forever.
    """
    symbol = symbol.upper()
    chain = [BY_NAME[provider]] if provider else \
        [p for p in PROVIDERS if p.supports(symbol, tf)]
    pager = next((p for p in chain if p.supports_paging), None)

    if pager is None:
        # No pager available — one page is all there is.
        return get_bars(symbol, tf, provider=provider)

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

    out.sort(key=lambda b: b.ts)
    return out


def get_bars(symbol: str, tf: str = "5m", count: int = 5000,
             provider: Optional[str] = None) -> List[Bar]:
    """Fetch bars, trying each provider that supports the instrument.

    A provider missing its key is skipped, not fatal — the next one gets a go.
    If every provider fails you get all their errors, because 'no data' with no
    reason is the worst possible failure here.
    """
    symbol = symbol.upper()
    chain = [BY_NAME[provider]] if provider else \
        [p for p in PROVIDERS if p.supports(symbol, tf)]

    if not chain:
        raise ProviderError(
            f"No provider covers {symbol} at {tf}. Known: "
            f"{', '.join(catalogue())}")

    errors = []
    for p in chain:
        if not p.supports(symbol, tf):
            errors.append(f"{p.name}: does not carry {symbol} at {tf}")
            continue
        try:
            bars = p.fetch(symbol, tf, count)
            if bars:
                return bars
            errors.append(f"{p.name}: returned no bars")
        except ProviderError as e:
            errors.append(f"{p.name}: {e}")

    raise ProviderError(f"All providers failed for {symbol} {tf}:\n  "
                        + "\n  ".join(errors))
