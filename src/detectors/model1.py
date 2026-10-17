"""Model #1 entry trigger. Rulebook §7.

Bearish: the last up-close bar that took out a high. When a later bar closes
below its low, entry sits at that bar's low, stop above its high.
Bullish mirrors it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..core.types import Bar, Direction, Timeframe


@dataclass
class Model1Setup:
    candle_index: int
    candle_ts: int
    trigger_index: int       # the bar whose close armed the entry
    entry: float
    stop: float
    direction: Direction
    timeframe: Timeframe

    @property
    def risk(self) -> float:
        return abs(self.stop - self.entry)

    def target_at_r(self, r: float) -> float:
        """Price at r multiples of risk. Reporting only — not a recommendation."""
        if self.direction is Direction.BEARISH:
            return self.entry - r * self.risk
        return self.entry + r * self.risk


def find_model1(
    bars: List[Bar],
    direction: Direction,
    search_from: int,
    tf: Timeframe,
    max_lookback: int = 20,
) -> Optional[Model1Setup]:
    """Find the first armed Model #1 at or after `search_from`.

    `search_from` should be the bar where the PLP was taken (Rulebook §6/§7) —
    Model #1 is only valid after that.

    Returns the first setup found, or None.
    """
    if direction is Direction.NONE:
        return None

    for trigger in range(search_from, len(bars)):
        bar = bars[trigger]

        # Walk back for the qualifying origin candle.
        for back in range(1, max_lookback + 1):
            j = trigger - back
            if j < 1:
                break
            cand = bars[j]

            if direction is Direction.BEARISH:
                qualifies = cand.is_up and cand.high > bars[j - 1].high
                armed = bar.close < cand.low
                if qualifies and armed:
                    return Model1Setup(
                        candle_index=j, candle_ts=cand.ts,
                        trigger_index=trigger,
                        entry=cand.low, stop=cand.high,
                        direction=direction, timeframe=tf,
                    )
            else:
                qualifies = cand.is_down and cand.low < bars[j - 1].low
                armed = bar.close > cand.high
                if qualifies and armed:
                    return Model1Setup(
                        candle_index=j, candle_ts=cand.ts,
                        trigger_index=trigger,
                        entry=cand.high, stop=cand.low,
                        direction=direction, timeframe=tf,
                    )

    return None
