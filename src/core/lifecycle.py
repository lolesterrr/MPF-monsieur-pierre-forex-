"""Bias invalidation and the CRT cap. Rulebook §11.1 and §11.2.

Your strategy doc flags both of these as undefined, so everything here is a
default I chose. They are the two most likely to be wrong, and they change
behaviour rather than presentation — argue with them first.

Why they matter: without §11.1 a bias set on Monday survives a Thursday
reversal, and without §11.2 the tool keeps offering setups after the trade
is already won.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .types import Bar, Direction, Level


@dataclass
class BiasState:
    direction: Direction
    set_at_ts: int
    rule: str
    invalidated_ts: Optional[int] = None
    invalidation_rule: Optional[str] = None

    @property
    def live(self) -> bool:
        return self.direction is not Direction.NONE and self.invalidated_ts is None


def invalidate_bias(
    state: BiasState,
    bars: List[Bar],
    reference_high: float,
    reference_low: float,
    start_index: int = 0,
) -> BiasState:
    """Rulebook §11.1 `[D]` — kill a bias mid-period.

    The reference levels are the ones the bias was read from: the previous
    week's high/low for a weekly bias, the previous day's for a daily one.

    Default rule: **a close beyond the opposite reference level**.

    Reasoning, so you can disagree precisely: the doc sets a bearish bias when
    price wicks above the previous high and closes back inside. The thing that
    says "that read was wrong" is price going on to *close* above it anyway —
    the same close/wick distinction used everywhere else in the rulebook. A
    wick alone is not enough, because a wick is what the strategy expects.

    Alternatives you might prefer:
      - close beyond by N pips (filters noise, adds a parameter)
      - two consecutive closes beyond (slower, fewer false kills)
      - invalidate on the opposite bias firing (simplest, but lags a day)
    """
    if not state.live:
        return state

    for i in range(start_index, len(bars)):
        bar = bars[i]
        if bar.ts <= state.set_at_ts:
            continue

        if state.direction is Direction.BEARISH and bar.close > reference_high:
            state.invalidated_ts = bar.ts
            state.invalidation_rule = "closed above the reference high"
            return state

        if state.direction is Direction.BULLISH and bar.close < reference_low:
            state.invalidated_ts = bar.ts
            state.invalidation_rule = "closed below the reference low"
            return state

    return state


@dataclass
class TargetProgress:
    """Rulebook §11.2 `[D]` — when to stop taking new CRTs.

    The doc says "keep taking third-candle CRTs in the bias direction until
    the daily target is reached" and does not cap it. Unlimited is almost
    certainly not what you meant: after three losers in a row toward the same
    target, the read is wrong, not unlucky.
    """
    target: Optional[Level]
    taken: int = 0
    consecutive_losses: int = 0
    reached_ts: Optional[int] = None
    stopped_reason: Optional[str] = None

    #: defaults — all three are [D]
    max_attempts: int = 3
    max_consecutive_losses: int = 2

    @property
    def open_for_business(self) -> bool:
        return self.stopped_reason is None and self.reached_ts is None

    def record(self, won: bool) -> "TargetProgress":
        self.taken += 1
        self.consecutive_losses = 0 if won else self.consecutive_losses + 1

        if self.consecutive_losses >= self.max_consecutive_losses:
            self.stopped_reason = (
                f"{self.consecutive_losses} losses in a row toward the same "
                f"target — the read is wrong, not unlucky")
        elif self.taken >= self.max_attempts:
            self.stopped_reason = (
                f"{self.taken} attempts at one target is the cap")
        return self

    def check_reached(self, bars: List[Bar], direction: Direction,
                      start_index: int = 0) -> "TargetProgress":
        """A target is reached when price trades into the level. Wick counts —
        this is about whether the move happened, not about entries."""
        if not self.target or self.reached_ts:
            return self

        for i in range(start_index, len(bars)):
            bar = bars[i]
            if direction is Direction.BEARISH and bar.low <= self.target.top:
                self.reached_ts = bar.ts
                self.stopped_reason = "daily target reached"
                return self
            if direction is Direction.BULLISH and bar.high >= self.target.bottom:
                self.reached_ts = bar.ts
                self.stopped_reason = "daily target reached"
                return self
        return self

    def to_dict(self) -> dict:
        return {
            "attempts": self.taken,
            "max_attempts": self.max_attempts,
            "consecutive_losses": self.consecutive_losses,
            "target_reached": self.reached_ts is not None,
            "accepting_new_setups": self.open_for_business,
            "stopped_because": self.stopped_reason,
        }
