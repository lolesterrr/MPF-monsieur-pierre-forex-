"""Bar loading and timeframe aggregation.

Real data drops in through `load_csv`. `synthetic` exists so the dashboard has
something to render before a feed is wired up — it is NOT market data and must
never be used to judge whether the strategy works.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from ..core.types import Bar, Timeframe

_NY = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

TF_SECONDS: Dict[Timeframe, int] = {
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
}


def load_csv(path: str) -> List[Bar]:
    """Read OHLC bars from CSV.

    Expects headers: time (ISO8601 or epoch seconds), open, high, low, close,
    and optionally volume. Rows are sorted by time; malformed rows raise rather
    than being silently skipped — a quietly dropped bar is a wrong backtest.
    """
    bars: List[Bar] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            raw = (row.get("time") or row.get("timestamp") or row.get("date") or "").strip()
            try:
                ts = int(raw) if raw.isdigit() else int(
                    datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
                bars.append(Bar(
                    ts=ts,
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                ))
            except (KeyError, ValueError) as e:
                raise ValueError(f"{path} line {n}: {e}") from e

    bars.sort(key=lambda b: b.ts)

    bad = [b for b in bars if not (b.low <= b.open <= b.high
                                   and b.low <= b.close <= b.high)]
    if bad:
        raise ValueError(f"{path}: {len(bad)} bars have OHLC outside high/low")

    return bars


def resample(bars: List[Bar], target: Timeframe,
             anchor_hour_utc: Optional[int] = None) -> List[Bar]:
    """Aggregate lower-timeframe bars upward, on the New York clock.

    D1, W1 and H4 are cut on New York time, the way the strategy reads them:
    the day and the week open at 17:00 NY (Rulebook §0), and 4H candles open
    at 17/21/01/05/09/13 NY. That grid is DST-aware, so a winter day opens at
    22:00 UTC and a summer day at 21:00 UTC.

    The old version took one fixed `anchor_hour_utc` (21) and cut 4H candles
    on UTC hours. The daily candles were therefore an hour wrong for half the
    year, and every 4H candle sat 1-2 hours off the NY grid, so the
    1am/5am/9am 4H filter could not be applied at all. `anchor_hour_utc` is
    still accepted so old callers do not break, and is ignored.

    Only the last bucket may be incomplete; it is dropped, because a live
    forming bar is not a closed bar and bias rules need closed bars.
    """
    if not bars:
        return []

    secs = TF_SECONDS[target]
    ny_grid = target in (Timeframe.H4, Timeframe.D1, Timeframe.W1)

    buckets: Dict[int, List[Bar]] = {}
    for b in bars:
        if ny_grid:
            key = _ny_bucket(b.ts, target)
        else:
            key = (b.ts // secs) * secs
        buckets.setdefault(key, []).append(b)

    out: List[Bar] = []
    for key in sorted(buckets):
        group = buckets[key]
        out.append(Bar(
            ts=key,
            open=group[0].open,
            high=max(g.high for g in group),
            low=min(g.low for g in group),
            close=group[-1].close,
            volume=sum(g.volume for g in group),
        ))

    # Drop a trailing partial bucket. For the NY grid the bucket's end is the
    # next bucket boundary, worked out on the NY clock; a W1 bucket ends at
    # Friday 17:00 NY, which is when the last bar of the week closes.
    last = bars[-1].ts + TF_SECONDS.get(_infer_tf(bars), 60)
    if out:
        end = (_ny_bucket_end(out[-1].ts, target) if ny_grid
               else out[-1].ts + secs)
        if last < end:
            out.pop()

    return out


_DAY_OPEN_NY = 17          # the forex day and week open, Rulebook §0


def _ny_offset(ts: int) -> int:
    """Seconds New York is ahead of UTC at `ts` (negative: -4h or -5h)."""
    return _ny_offset_hour(ts // 3600)


@lru_cache(maxsize=65536)
def _ny_offset_hour(hour: int) -> int:
    d = datetime.fromtimestamp(hour * 3600, tz=_UTC).astimezone(_NY)
    return int(d.utcoffset().total_seconds())


def _ny_bucket(ts: int, tf: Timeframe) -> int:
    """UTC open of the NY-clock bucket holding `ts`.

    Shift into 'NY time minus 17h', where every forex day starts at 00:00,
    floor there, and shift back. DST changes happen at 02:00 on a Sunday,
    while the market is shut, so the offset is constant inside any bucket.
    """
    off = _ny_offset(ts)
    shift = off - _DAY_OPEN_NY * 3600
    local = ts + shift
    if tf is Timeframe.W1:
        # Day 0 of the epoch is a Thursday; shifted, the week starts Sunday
        # (day 3), which is Sunday 17:00 NY in real time.
        week, sun = 7 * 86400, 3 * 86400
        key = ((local - sun) // week) * week + sun
    else:
        span = TF_SECONDS[tf]
        key = (local // span) * span
    return key - shift


def _ny_bucket_end(open_ts: int, tf: Timeframe) -> int:
    if tf is Timeframe.W1:
        return open_ts + 5 * 86400    # Sunday 17:00 -> Friday 17:00 NY
    return open_ts + TF_SECONDS[tf]


def _infer_tf(bars: List[Bar]) -> Timeframe:
    if len(bars) < 2:
        return Timeframe.M5
    gap = min(bars[i + 1].ts - bars[i].ts for i in range(min(20, len(bars) - 1)))
    for tf, s in TF_SECONDS.items():
        if s == gap:
            return tf
    return Timeframe.M5


def synthetic(n: int = 2400, seed: int = 7,
              start: datetime | None = None,
              step_seconds: int = 300,
              start_price: float = 1.0850) -> List[Bar]:
    """Plausible-looking 5m bars for UI work. NOT market data.

    Random walk with session-dependent volatility and occasional sweeps, so the
    detectors have something shaped like price to chew on. Any confluence it
    produces is noise by construction.
    """
    rng = random.Random(seed)
    start = start or datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    price = start_price
    bars: List[Bar] = []

    for i in range(n):
        ts = start + timedelta(seconds=i * step_seconds)
        hour = ts.hour

        # Asia quiet, London/NY busy.
        vol = 0.00012 if 0 <= hour < 7 else (0.00045 if 7 <= hour < 17 else 0.00020)

        drift = rng.gauss(0, vol)
        if rng.random() < 0.015:          # occasional impulse
            drift += rng.choice([-1, 1]) * vol * rng.uniform(4, 9)

        o = price
        c = o + drift
        wick = abs(rng.gauss(0, vol * 0.9))
        if rng.random() < 0.06:           # occasional stop-hunt wick
            wick *= rng.uniform(2.5, 5.0)

        h = max(o, c) + wick
        l = min(o, c) - abs(rng.gauss(0, vol * 0.9))
        price = c

        bars.append(Bar(ts=int(ts.timestamp()),
                        open=round(o, 5), high=round(h, 5),
                        low=round(l, 5), close=round(c, 5),
                        volume=round(rng.uniform(50, 400), 1)))

    return bars
