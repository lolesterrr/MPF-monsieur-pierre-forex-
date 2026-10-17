"""Asian range. Rulebook §9.1.

Window is 20:00-00:00 New York, DST-aware. That window crosses midnight NY,
so the range "belongs" to the trading day that follows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .types import Bar, PIP

NY = ZoneInfo("America/New_York")

ASIA_START_HOUR = 20
ASIA_END_HOUR = 0


@dataclass
class AsianRange:
    date_ny: str          # the NY date the range applies TO
    high: float
    low: float
    start_ts: int
    end_ts: int
    high_index: int
    low_index: int

    @property
    def eq(self) -> float:
        return (self.high + self.low) / 2

    @property
    def size_pips(self) -> float:
        return (self.high - self.low) / PIP

    def near(self, price: float, tolerance_pips: float = 5.0) -> Optional[str]:
        """Is `price` sitting on one of the edges? Rulebook §9.4."""
        if abs(price - self.high) / PIP <= tolerance_pips:
            return "asia_high"
        if abs(price - self.low) / PIP <= tolerance_pips:
            return "asia_low"
        if abs(price - self.eq) / PIP <= tolerance_pips:
            return "asia_50"
        return None


def _ny(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=ZoneInfo("UTC")).astimezone(NY)


def _session_date(dt: datetime) -> str:
    """Which trading day does this bar's Asian session belong to?

    20:00-23:59 NY on Monday -> the range for Tuesday.
    00:00 NY exactly ends the window.
    """
    if dt.hour >= ASIA_START_HOUR:
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def build_asian_ranges(bars: List[Bar]) -> Dict[str, AsianRange]:
    """Group bars into Asian sessions and freeze each range at 00:00 NY.

    Feed this intraday bars (5m or 15m). Daily bars will produce nonsense.
    """
    buckets: Dict[str, List[tuple[int, Bar]]] = {}

    for i, bar in enumerate(bars):
        dt = _ny(bar.ts)
        # Window is 20:00 -> 00:00 NY. 00:00 is the hard end, so only bars at
        # or after 20:00 on the prior calendar day are in the session.
        if dt.hour < ASIA_START_HOUR:
            continue
        buckets.setdefault(_session_date(dt), []).append((i, bar))

    ranges: Dict[str, AsianRange] = {}
    for date, items in buckets.items():
        if not items:
            continue
        hi_i, hi_bar = max(items, key=lambda x: x[1].high)
        lo_i, lo_bar = min(items, key=lambda x: x[1].low)
        ranges[date] = AsianRange(
            date_ny=date,
            high=hi_bar.high, low=lo_bar.low,
            start_ts=items[0][1].ts, end_ts=items[-1][1].ts,
            high_index=hi_i, low_index=lo_i,
        )

    return ranges


def classify_break(bars_m5: List[Bar], rng: AsianRange,
                   start_index: int) -> Optional[dict]:
    """Scenario A vs B. Rulebook §9.2 / §9.3.

    A true break needs a 5-MINUTE CLOSE beyond the edge. A wick beyond that
    closes back inside is a false break, which is Scenario A.

    Returns the FIRST resolved event after `start_index`, or None.
    """
    for j in range(start_index, len(bars_m5)):
        bar = bars_m5[j]

        if bar.close > rng.high:
            return {"scenario": "B", "side": "high", "index": j,
                    "type": "true_break", "ts": bar.ts}
        if bar.close < rng.low:
            return {"scenario": "B", "side": "low", "index": j,
                    "type": "true_break", "ts": bar.ts}

        if bar.high > rng.high and bar.close <= rng.high:
            return {"scenario": "A", "side": "high", "index": j,
                    "type": "false_break", "ts": bar.ts}
        if bar.low < rng.low and bar.close >= rng.low:
            return {"scenario": "A", "side": "low", "index": j,
                    "type": "false_break", "ts": bar.ts}

    return None
