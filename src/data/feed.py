"""Bar loading and timeframe aggregation.

Real data drops in through `load_csv`. `synthetic` exists so the dashboard has
something to render before a feed is wired up — it is NOT market data and must
never be used to judge whether the strategy works.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from ..core.types import Bar, Timeframe

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
             anchor_hour_utc: int = 21) -> List[Bar]:
    """Aggregate lower-timeframe bars upward.

    `anchor_hour_utc` is the daily/weekly open (Rulebook §0: 17:00 New York,
    which is 21:00 or 22:00 UTC depending on DST — pass the right one, or use
    pre-aggregated daily bars from your feed and skip this).

    Only the last bucket may be incomplete; it is dropped, because a live
    forming bar is not a closed bar and bias rules need closed bars.
    """
    if not bars:
        return []

    secs = TF_SECONDS[target]
    offset = (anchor_hour_utc * 3600) % secs if target in (
        Timeframe.D1, Timeframe.W1) else 0

    buckets: Dict[int, List[Bar]] = {}
    for b in bars:
        key = ((b.ts - offset) // secs) * secs + offset
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

    # Drop a trailing partial bucket.
    if out and bars[-1].ts < out[-1].ts + secs - TF_SECONDS.get(
            _infer_tf(bars), 60):
        out.pop()

    return out


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
