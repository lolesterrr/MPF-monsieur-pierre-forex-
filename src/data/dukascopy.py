"""Dukascopy public datafeed — no account, no key, no country check.

This exists because OANDA is a *broker*: it needs per-country regulatory
approval, which is why a Ugandan signup gets refused. Dukascopy's historical
datafeed is a public static file server with no registration at all, so
geography never comes into it.

It is also the deepest free history available — years of tick data, which is
what the backtest actually needs.

THE CATCH, STATED UP FRONT
--------------------------
This is an undocumented binary endpoint. The record layout is known from
community reverse-engineering and sources disagree on whether prices are
int32-scaled or float32. Rather than guess, `_decode_ticks` parses BOTH ways
and keeps whichever produces prices in a plausible range for the instrument,
and raises if neither does.

That means a silent wrong-scale failure — the kind that poisons a backtest
without ever looking broken — turns into a loud error instead.

Run `python3 -m src.data.dukascopy --verify EURUSD` before trusting it.
"""

from __future__ import annotations

import lzma
import struct
import time as _time
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from ..core.types import Bar

BASE = "https://datafeed.dukascopy.com/datafeed"

#: canonical -> Dukascopy instrument code
SYMBOLS: Dict[str, str] = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD", "USDCAD": "USDCAD", "USDCHF": "USDCHF",
    "NZDUSD": "NZDUSD", "EURGBP": "EURGBP", "EURJPY": "EURJPY",
    "GBPJPY": "GBPJPY",
    "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD",
    "NAS100": "USATECHIDXUSD", "SPX500": "USA500IDXUSD", "US30": "USA30IDXUSD",
    "OIL": "LIGHTCMDUSD",
}

#: Plausible price band per instrument, used to decide which decode is right.
#: Wide on purpose — this catches scale errors (1.08 vs 108000), not drift.
SANE_RANGE: Dict[str, Tuple[float, float]] = {
    "EURUSD": (0.5, 2.0), "GBPUSD": (0.8, 2.5), "USDJPY": (50, 250),
    "AUDUSD": (0.3, 1.5), "USDCAD": (0.8, 2.0), "USDCHF": (0.5, 2.0),
    "NZDUSD": (0.3, 1.5), "EURGBP": (0.5, 1.5), "EURJPY": (60, 250),
    "GBPJPY": (80, 320),
    "XAUUSD": (200, 10000), "XAGUSD": (5, 200),
    "NAS100": (1000, 60000), "SPX500": (500, 20000), "US30": (5000, 80000),
    "OIL": (5, 300),
}

#: Price scaling exponent. JPY pairs and metals quote to 3dp, majors to 5dp.
POINT = {
    "USDJPY": 1e3, "EURJPY": 1e3, "GBPJPY": 1e3,
    "XAUUSD": 1e3, "XAGUSD": 1e3,
    "NAS100": 1e3, "SPX500": 1e3, "US30": 1e3, "OIL": 1e3,
}
DEFAULT_POINT = 1e5

TICK_STRUCT_INT = struct.Struct(">IIIff")     # ms, ask, bid, askvol, bidvol
TICK_STRUCT_FLT = struct.Struct(">Ifffff"[:6])  # ms, ask, bid, askvol  (20B)


class DukascopyError(RuntimeError):
    pass


def _url(symbol: str, when: datetime) -> str:
    """Path months are ZERO-indexed. January is 00, not 01 — the single most
    common mistake with this feed."""
    code = SYMBOLS[symbol]
    return (f"{BASE}/{code}/{when.year:04d}/{when.month - 1:02d}/"
            f"{when.day:02d}/{when.hour:02d}h_ticks.bi5")


def _fetch_hour(symbol: str, when: datetime, timeout: int = 30,
                retries: int = 4) -> bytes:
    """One hour of ticks, with backoff.

    503 and 429 mean we are asking too fast, not that the data is missing.
    Hammering straight through produced "21 failures, giving up" on a perfectly
    good symbol. Backing off fixes it.
    """
    url = _url(symbol, when)
    delay = 1.0

    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""      # market shut that hour — normal, not an error
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise DukascopyError(
                f"HTTP {e.code} for {symbol} {when:%Y-%m-%d %Hh}") from e
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise DukascopyError(
                f"{type(e).__name__} fetching {symbol}: {e}") from e

    return b""


def _decode_ticks(raw: bytes, hour_start: datetime, symbol: str) -> List[dict]:
    """Decompress and parse one hour of ticks.

    Tries the int32-scaled layout first, then float32, and keeps whichever
    yields prices inside SANE_RANGE. If neither does, raises rather than
    returning plausible-looking garbage.
    """
    if not raw:
        return []

    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError as e:
        raise DukascopyError(f"not LZMA — feed format may have changed: {e}") from e

    if len(data) % 20:
        raise DukascopyError(
            f"{len(data)} bytes is not a multiple of 20; record layout changed")

    lo, hi = SANE_RANGE.get(symbol, (1e-6, 1e9))
    point = POINT.get(symbol, DEFAULT_POINT)
    base_ms = hour_start.timestamp() * 1000

    def attempt(mode: str) -> Optional[List[dict]]:
        out = []
        for off in range(0, len(data), 20):
            chunk = data[off:off + 20]
            if mode == "int":
                ms, ask_i, bid_i, av, bv = TICK_STRUCT_INT.unpack(chunk)
                ask, bid = ask_i / point, bid_i / point
            else:
                ms, ask, bid, av, bv = struct.unpack(">Iffff", chunk)
            out.append({"ts": (base_ms + ms) / 1000.0,
                        "bid": bid, "ask": ask, "vol": (av or 0) + (bv or 0)})
        if not out:
            return []
        sample = [t["bid"] for t in out[:200] if t["bid"] > 0]
        if not sample:
            return None
        mid = sorted(sample)[len(sample) // 2]
        return out if lo <= mid <= hi else None

    for mode in ("int", "float"):
        got = attempt(mode)
        if got is not None:
            return got

    raise DukascopyError(
        f"{symbol}: neither int32-scaled nor float32 decoding produced prices "
        f"inside {lo}–{hi}. Run --verify and check the layout before trusting "
        f"any of this data.")


def _to_bars(ticks: List[dict], seconds: int) -> List[Bar]:
    """Aggregate ticks into OHLC bars on the mid price."""
    buckets: Dict[int, List[dict]] = {}
    for t in ticks:
        key = int(t["ts"] // seconds) * seconds
        buckets.setdefault(key, []).append(t)

    bars = []
    for key in sorted(buckets):
        group = buckets[key]
        mids = [(t["bid"] + t["ask"]) / 2 for t in group]
        bars.append(Bar(
            ts=key, open=mids[0], high=max(mids),
            low=min(mids), close=mids[-1],
            volume=sum(t["vol"] for t in group),
        ))
    return bars


TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
              "1h": 3600, "4h": 14400, "1d": 86400}


def fetch(symbol: str, tf: str = "5m", days: int = 30,
          end: Optional[datetime] = None, quiet: bool = False,
          workers: int = 8, deadline: Optional[float] = None) -> List[Bar]:
    """Download `days` of history and aggregate to `tf`.

    One HTTP request per hour of history, so 30 days is ~720 requests. Slow
    but free and deep. Weekends 404 and are skipped silently, which is correct
    — the forex market is shut.
    """
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise DukascopyError(
            f"{symbol} not mapped. Known: {', '.join(sorted(SYMBOLS))}")
    if tf not in TF_SECONDS:
        raise DukascopyError(f"{tf} not supported. Use {', '.join(TF_SECONDS)}")

    end = (end or datetime.now(timezone.utc)).replace(
        minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    # Only hours the market was actually open.
    from ..core.market_hours import is_open
    wanted: List[datetime] = []
    cursor = start
    while cursor < end:
        if is_open(int(cursor.timestamp())):
            wanted.append(cursor)
        cursor += timedelta(hours=1)

    if not wanted:
        raise DukascopyError(
            f"No open market hours in the last {days} days for {symbol}.")

    ticks: List[dict] = []
    failures: List[str] = []

    def one(when: datetime):
        try:
            raw = _fetch_hour(symbol, when)
            return _decode_ticks(raw, when, symbol) if raw else []
        except DukascopyError as e:
            failures.append(str(e))
            return []

    # Sequential fetching of 700+ hours is unusable. Modest concurrency keeps
    # it polite — the backoff above handles any 503 that still slips through.
    # `deadline` is a time.monotonic() value. Honouring it matters: an
    # unreachable host retried with backoff across 700 hours will hold a web
    # request open until the browser gives up, and the app just looks frozen.
    stopped_early = False
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(one, w) for w in wanted]
        for n, fut in enumerate(futures, start=1):
            if deadline is not None and _time.monotonic() > deadline:
                stopped_early = True
                break
            # Give each future only the time that is actually left, so one
            # hanging connection cannot eat the whole budget by itself.
            left = None if deadline is None else max(
                0.1, deadline - _time.monotonic())
            try:
                ticks.extend(fut.result(timeout=left))
            except FuturesTimeout:
                stopped_early = True
                break
            if not quiet and n % 120 == 0:
                print(f"    {n}/{len(wanted)} hours, {len(ticks)} ticks")
    finally:
        # cancel_futures matters: plain shutdown() waits for every queued
        # task, which is how a 6s budget turned into 16s in testing.
        pool.shutdown(wait=False, cancel_futures=True)

    # Judge on the proportion that failed, not a running count. The old
    # counter never reset on success, so a long pull with scattered blips
    # tripped a "21 consecutive failures" error that was never consecutive.
    if failures and len(failures) > max(10, len(wanted) * 0.25):
        raise DukascopyError(
            f"{len(failures)} of {len(wanted)} hours failed for {symbol}. "
            f"First: {failures[0]}")

    if stopped_early and not ticks:
        raise DukascopyError(
            f"{symbol}: ran out of time before any data arrived. The feed is "
            f"slow or unreachable from here; try a keyed provider for live use.")

    if not ticks:
        raise DukascopyError(
            f"No ticks for {symbol} over {days} days "
            f"({len(failures)} request failures). Either the symbol code is "
            f"wrong or the feed is unreachable from here.")

    return _to_bars(ticks, TF_SECONDS[tf])


def verify(symbol: str = "EURUSD") -> None:
    """Fetch one hour and print it, so you can eyeball the decode.

        python3 -m src.data.dukascopy --verify EURUSD

    Do this once before trusting any backtest built on this provider.
    """
    # Step back to a weekday mid-session hour.
    when = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    when -= timedelta(hours=24)
    while when.weekday() >= 5:
        when -= timedelta(days=1)
    when = when.replace(hour=14)        # London/NY overlap, guaranteed busy

    print(f"  {_url(symbol, when)}")
    raw = _fetch_hour(symbol, when)
    print(f"  {len(raw)} compressed bytes")
    if not raw:
        print("  empty — try a different hour (holiday?) ")
        return

    ticks = _decode_ticks(raw, when, symbol)
    print(f"  {len(ticks)} ticks decoded")
    lo, hi = SANE_RANGE.get(symbol, (0, 0))
    print(f"  sanity band for {symbol}: {lo} – {hi}")
    for t in ticks[:3]:
        ts = datetime.fromtimestamp(t["ts"], tz=timezone.utc)
        print(f"    {ts:%H:%M:%S}  bid {t['bid']:.5f}  ask {t['ask']:.5f}")

    bars = _to_bars(ticks, 300)
    print(f"  -> {len(bars)} 5m bars")
    for bar in bars[:3]:
        print(f"    {datetime.fromtimestamp(bar.ts, tz=timezone.utc):%H:%M}  "
              f"O {bar.open:.5f} H {bar.high:.5f} "
              f"L {bar.low:.5f} C {bar.close:.5f}")
    print("\n  If those prices look like real quotes, the decode is right.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Dukascopy public datafeed")
    p.add_argument("--verify", metavar="SYMBOL", nargs="?", const="EURUSD")
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--tf", default="5m")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--out", help="write CSV here")
    a = p.parse_args()

    if a.verify:
        verify(a.verify)
    else:
        bars = fetch(a.symbol, a.tf, a.days)
        print(f"  {len(bars)} bars")
        if a.out:
            import csv
            with open(a.out, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["time", "open", "high", "low", "close", "volume"])
                for b in bars:
                    w.writerow([b.ts, b.open, b.high, b.low, b.close, b.volume])
            print(f"  wrote {a.out}")
