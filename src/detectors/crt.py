"""CRT detection. Rulebook §5.

Three bars: C1 is the range, C2 sweeps one side and closes back inside,
C3 is the only bar traded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..core.market_hours import next_open
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
    #: C2's open. The candle you actually go and LOOK at to confirm the sweep —
    #: C3 may not exist yet, and when it does not, there is nothing to focus.
    c2_ts: int = 0
    #: True when C3 has not printed yet: the setup is live and ahead of you.
    #: False means C3 is a closed bar and this CRT is history.
    c3_pending: bool = False

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
    which is the whole point.

    That sentence used to be a lie. The loop ran to `len(bars) - 1` and read
    `bars[i + 1].ts`, so a C3 bar had to EXIST before the pattern would be
    reported — and resample() drops the forming bar, so "exists" meant
    "already closed". The newest CRT you could ever be shown was one whose
    trade candle was over. On a daily that is a full day late: the setup
    appeared at Wednesday's close, having been tradeable all Wednesday.

    Nothing about the detection needed C3. C1 gives the range, C2 sweeps it
    and closes back inside, and both are shut by the time we look. C3 is the
    CONSEQUENCE, not the evidence. So its timestamp is now projected rather
    than read, and a CRT whose C3 has not printed is reported with
    `c3_pending=True` — which is the only kind you can still trade.

    Still no lookahead: C3's OHLC is never consulted, before or after.

    If `bias` is given, CRTs against it are dropped (Rulebook §5.1).
    """
    out: List[CRT] = []

    for i in range(1, len(bars)):
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

        # When C3 is already on the chart its real timestamp beats any
        # projection. When it is not, project — and skip the weekend, or a
        # Friday C2 would announce a C3 that opens on Saturday.
        pending = c3_index >= len(bars)
        c3_ts = (next_open(c2.ts + tf.seconds) if pending
                 else bars[c3_index].ts)

        out.append(CRT(
            c1_index=i - 1, c2_index=i, c3_index=c3_index,
            direction=direction,
            range_high=c1.high, range_low=c1.low,
            timeframe=tf,
            c3_ts=c3_ts,
            c2_ts=c2.ts,
            c3_pending=pending,
        ))

    return out
