"""Weekly and daily bias. Rulebook §3.

Both read a CLOSED bar and apply the result to the NEXT one. Any function here
that peeks at the bar it is biasing is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .types import Bar, Direction


@dataclass
class BiasResult:
    direction: Direction
    rule: str          # which numbered rule fired — shown in the UI
    source_index: int  # the closed bar it was read from

    @property
    def tradeable(self) -> bool:
        return self.direction is not Direction.NONE


def weekly_bias(prev: Bar, current: Bar, index: int) -> BiasResult:
    """Rulebook §3.1. `current` must be CLOSED. Applies to the week after it.

    Precedence: close beats wick. Outside week resolves to rule 1.
    """
    if current.close < prev.low:
        return BiasResult(Direction.BEARISH, "W1 close below prev low", index)
    if current.close > prev.high:
        return BiasResult(Direction.BULLISH, "W2 close above prev high", index)
    if current.high > prev.high:
        return BiasResult(Direction.BEARISH, "W3 wick above prev high", index)
    if current.low < prev.low:
        return BiasResult(Direction.BULLISH, "W4 wick below prev low", index)
    return BiasResult(Direction.NONE, "W5 inside week — no bias", index)


def daily_bias(prev: Bar, current: Bar, index: int) -> BiasResult:
    """Rulebook §3.2. Applies to the day AFTER `current`.

    Note the outside-day carve-out: if both sides were wicked and price closed
    back inside, there is no read. Your doc did not cover it; skipping is safer
    than picking a side.
    """
    wick_up = current.high > prev.high and current.close <= prev.high
    wick_dn = current.low < prev.low and current.close >= prev.low

    if wick_up and wick_dn:
        return BiasResult(Direction.NONE, "D5 outside day — skip", index)
    if current.close > prev.high:
        return BiasResult(Direction.BULLISH, "D1 close above prev high", index)
    if current.close < prev.low:
        return BiasResult(Direction.BEARISH, "D2 close below prev low", index)
    if wick_up:
        return BiasResult(Direction.BEARISH, "D3 wick above prev high", index)
    if wick_dn:
        return BiasResult(Direction.BULLISH, "D4 wick below prev low", index)
    return BiasResult(Direction.NONE, "D6 inside day — skip", index)


def h1_profile(swing_highs: List[float], swing_lows: List[float]) -> BiasResult:
    """Rulebook §3.3 — the Model 2 gate.

    Needs the last two CONFIRMED swing highs and lows, oldest first.
    Unclear is a real answer here: your doc says skip the trade.
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return BiasResult(Direction.NONE, "H1 insufficient swings", -1)

    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1] > swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1] < swing_lows[-2]

    if hh and hl:
        return BiasResult(Direction.BULLISH, "H1 higher highs + higher lows", -1)
    if lh and ll:
        return BiasResult(Direction.BEARISH, "H1 lower highs + lower lows", -1)
    return BiasResult(Direction.NONE, "H1 profile unclear — skip", -1)


def premium_discount(high: float, low: float, price: float) -> tuple[str, float]:
    """Rulebook §4. Returns (zone, eq)."""
    eq = (high + low) / 2
    if price > eq:
        return "premium", eq
    if price < eq:
        return "discount", eq
    return "equilibrium", eq


def resolve_stack(weekly: Optional[BiasResult],
                  daily: Optional[BiasResult],
                  h1: Optional[BiasResult]) -> dict:
    """The top-down panel: weekly -> daily -> 1H, and whether they agree.

    Agreement is reported, never assumed. Two out of three is not alignment.
    """
    layers = {"weekly": weekly, "daily": daily, "h1": h1}
    present = [b.direction for b in layers.values()
               if b is not None and b.direction is not Direction.NONE]

    aligned = len(present) > 0 and len(set(present)) == 1
    return {
        "layers": {k: (v.direction.value if v else None) for k, v in layers.items()},
        "rules": {k: (v.rule if v else None) for k, v in layers.items()},
        "aligned": aligned,
        "direction": present[0].value if aligned else None,
        "layers_with_bias": len(present),
    }
