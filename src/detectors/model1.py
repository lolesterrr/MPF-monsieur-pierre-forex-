"""Model #1 entry trigger. Rulebook §7.

Bullish: once the PLP has been taken, watch the last down-close candle in the
move up. When a later candle CLOSES above that candle's high, Model #1 is
armed. The stop goes below that candle's low, plus a small buffer for spread
and stray wicks. Bearish mirrors it: last up-close candle, armed by a close
below its low, stop above its high.

What changed, from the strategy's author:

- The candle no longer has to have made a lower low. "It's the last down-close
  candle in the move up — often the one that liquidates the PLP, but it
  doesn't have to be."
- It runs on the ENTRY timeframe (15M or 5M for a daily CRT, 5M for a 4H CRT),
  not on the PLP's timeframe. See replay.entry_timeframe.
- Two possible entries come out of one setup: at the trigger candle's close,
  or a limit back at the origin candle's high. Which one is used depends on
  the RR to T1, which is the caller's call because it knows the targets.
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
    entry: float             # the limit level: origin candle's high (bullish)
    stop: float              # origin candle's low minus the buffer (bullish)
    direction: Direction
    timeframe: Timeframe
    trigger_close: float = 0.0   # the market entry, if taken at the close
    trigger_ts: int = 0

    @property
    def risk(self) -> float:
        return abs(self.stop - self.entry)

    @property
    def armed_ts(self) -> int:
        """Open time of the bar after the trigger — when the trigger candle
        CLOSED, the earliest moment any entry can exist."""
        return self.trigger_ts + self.timeframe.seconds

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
    stop_buffer: float = 0.0,
    until_ts: Optional[int] = None,
) -> Optional[Model1Setup]:
    """Find the first armed Model #1 at or after `search_from`.

    `search_from` is the bar on THIS timeframe where the PLP was taken. Candles
    before it belong to the move into the PLP, not the move up out of it.

    `until_ts`: the trigger candle must have closed by then (end of C3).
    """
    if direction is Direction.NONE:
        return None

    bullish = direction is Direction.BULLISH
    secs = tf.seconds
    origin: Optional[int] = None

    for i in range(max(0, search_from), len(bars)):
        bar = bars[i]
        if until_ts is not None and bar.ts + secs > until_ts:
            break

        if origin is not None:
            cand = bars[origin]
            armed = bar.close > cand.high if bullish else bar.close < cand.low
            if armed:
                return Model1Setup(
                    candle_index=origin, candle_ts=cand.ts, trigger_index=i,
                    entry=cand.high if bullish else cand.low,
                    stop=(cand.low - stop_buffer) if bullish
                    else (cand.high + stop_buffer),
                    direction=direction, timeframe=tf,
                    trigger_close=bar.close, trigger_ts=bar.ts,
                )

        if (bar.is_down if bullish else bar.is_up):
            origin = i

    return None
