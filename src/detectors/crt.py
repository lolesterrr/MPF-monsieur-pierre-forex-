"""CRT detection. Rulebook §5.

Three bars: C1 is the range, C2 sweeps one side and closes back inside,
C3 is the only bar traded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..core.types import Bar, Direction, Timeframe


@dataclass
class CRT:
    c1_index: int
    c2_index: int
    c3_index: int
    direction: Direction
    range_high: float
    range_low: float
    timeframe: Timeframe
    c3_ts: int

    @property
    def eq(self) -> float:
        """50% of the CRT — first exit, closes 60%."""
        return (self.range_high + self.range_low) / 2

    @property
    def opposing(self) -> float:
        """Second exit — closes the remaining 40%."""
        return self.range_low if self.direction is Direction.BEARISH else self.range_high

    @property
    def id(self) -> str:
        return f"{self.timeframe.value}:crt:{self.c3_ts}"


def find_crts(bars: List[Bar], tf: Timeframe,
              bias: Optional[Direction] = None) -> List[CRT]:
    """Scan for CRT patterns.

    Bearish: C2 wicks ABOVE C1's high and closes back inside C1's range.
    Bullish: C2 wicks BELOW C1's low  and closes back inside C1's range.

    Detected the moment C2 closes, so C3 (the trade bar) is still ahead —
    which is the whole point. No lookahead.

    If `bias` is given, CRTs against it are dropped (Rulebook §5.1).
    """
    out: List[CRT] = []

    for i in range(1, len(bars) - 1):
        c1, c2 = bars[i - 1], bars[i]
        c3_index = i + 1

        inside = c1.low <= c2.close <= c1.high
        if not inside:
            continue

        direction: Optional[Direction] = None
        if c2.high > c1.high and c2.low >= c1.low:
            direction = Direction.BEARISH
        elif c2.low < c1.low and c2.high <= c1.high:
            direction = Direction.BULLISH
        elif c2.high > c1.high and c2.low < c1.low:
            # Swept both sides. Ambiguous — skip rather than guess.
            continue

        if direction is None:
            continue
        if bias is not None and bias is not Direction.NONE and direction is not bias:
            continue

        out.append(CRT(
            c1_index=i - 1, c2_index=i, c3_index=c3_index,
            direction=direction,
            range_high=c1.high, range_low=c1.low,
            timeframe=tf,
            c3_ts=bars[c3_index].ts,
        ))

    return out
