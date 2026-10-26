"""The confluence engine. Rulebook §6, §8, §10.

This ties the detectors together into the top-down view: which levels exist,
which of them line up, and how many of the strategy's conditions are met.

It reports. It does not recommend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .types import (
    PIP, Bar, Direction, Level, LevelStatus, LevelType, Swing, Timeframe,
)


# ---------------------------------------------------------------- PLP

@dataclass
class PLP:
    """Pre-open liquidity pool. Rulebook §6.

    Your doc defines it by outcome ("a fake structure shift"), which is only
    knowable afterwards. This is the before-the-fact proxy: resting liquidity
    on the side the bias says price should reach for first.
    """
    price: float
    swing_index: int
    swing_ts: int
    timeframe: Timeframe
    direction: Direction        # the BIAS it serves, not the swing's own side
    taken_index: Optional[int] = None

    @property
    def is_taken(self) -> bool:
        return self.taken_index is not None


def mark_plp(
    bars: List[Bar],
    swings: List[Swing],
    bias: Direction,
    as_of_index: int,
    tf: Timeframe,
    max_lookback: int = 48,
    require_mss: bool = True,
) -> Optional[PLP]:
    """Pick the PLP at `as_of_index` (normally the bar C3 opens on).

    Bearish bias -> price should reach UP for a swing high first, then fall.
    Bullish bias -> price should reach DOWN for a swing low first, then rise.

    Only swings CONFIRMED by as_of_index and not yet swept by then qualify.

    And not just any swing: the PLP is "a low that broke a high" (bullish) —
    the low the rally started from, where that rally went on to take out
    (a wick is enough) the swing high before it. When several qualify, the
    most recent one is the PLP (author confirmed). Taking that low would look like a bearish MSS,
    which is exactly the trap. Bearish mirrors it. `require_mss=False` gives
    the old nearest-swing behaviour.
    """
    if bias is Direction.NONE or as_of_index >= len(bars):
        return None

    # Bearish bias hunts a swing high; bullish hunts a swing low.
    want_high = bias is Direction.BEARISH
    floor_index = max(0, as_of_index - max_lookback)

    candidates = [
        s for s in swings
        if s.is_high == want_high
        and floor_index <= s.index
        and s.confirmed_index <= as_of_index
        and (s.swept_index is None or s.swept_index > as_of_index)
        and (not require_mss or broke_structure(bars, swings, s, as_of_index))
    ]
    if not candidates:
        return None

    # Nearest in time — the freshest resting pool.
    chosen = max(candidates, key=lambda s: s.index)

    taken = None
    for j in range(as_of_index, len(bars)):
        if want_high and bars[j].high > chosen.price:
            taken = j
            break
        if not want_high and bars[j].low < chosen.price:
            taken = j
            break

    return PLP(
        price=chosen.price, swing_index=chosen.index, swing_ts=chosen.ts,
        timeframe=tf, direction=bias, taken_index=taken,
    )


def broke_structure(bars: List[Bar], swings: List[Swing], s: Swing,
                    as_of_index: int) -> bool:
    """Did the leg out of swing `s` break the opposing swing before it?

    Bullish (s is a low): take the most recent swing high formed before s.
    Between s and `as_of_index`, a bar must trade above that high — a wick is
    enough (author: "even a wick works") — and price
    must not trade below s first — otherwise the rally did not start from s.
    Bearish mirrors it. Only bars up to `as_of_index` are read.
    """
    prior = [o for o in swings if o.is_high != s.is_high and o.index < s.index]
    if not prior:
        return False
    ref = max(prior, key=lambda o: o.index)
    for j in range(s.index + 1, min(as_of_index, len(bars) - 1) + 1):
        b = bars[j]
        if s.is_high:
            if b.high > s.price:
                return False
            if b.low < ref.price:
                return True
        else:
            if b.low < s.price:
                return False
            if b.high > ref.price:
                return True
    return False


# ---------------------------------------------------------------- level pool

@dataclass
class LevelPool:
    """Every FVG, order block and swing across timeframes, in one bucket.

    Rulebook §2. The daily target is drawn from here (§3.2 of the strategy doc:
    only FVG, order block, or swing qualify).
    """
    levels: List[Level] = field(default_factory=list)

    def add(self, levels: List[Level]) -> "LevelPool":
        self.levels.extend(levels)
        return self

    def valid(self) -> List[Level]:
        return [l for l in self.levels if l.status is not LevelStatus.INVALIDATED]

    def in_direction(self, d: Direction) -> List[Level]:
        return [l for l in self.valid() if l.direction is d]

    def near(self, price: float, tolerance_pips: float = 5.0,
             exclude_tf: Optional[Timeframe] = None) -> List[Level]:
        """Levels sitting within `tolerance_pips` of a price. The alignment check."""
        out = []
        for l in self.valid():
            if exclude_tf is not None and l.timeframe is exclude_tf:
                continue
            if l.distance_pips(price) <= tolerance_pips:
                out.append(l)
        return out

    def pick_target(self, bias: Direction, price: float, eq: float) -> Optional[Level]:
        """Rulebook §4 + strategy doc §1.2.

        Target must sit on the opposite side of the daily 50% from price.
        Where several qualify, a swing wins — your doc says so explicitly.
        """
        if bias is Direction.NONE:
            return None

        want_below = price > eq  # in premium -> target a low in discount
        pool = []
        for l in self.valid():
            on_right_side = (l.top < eq) if want_below else (l.bottom > eq)
            beyond_price = (l.top < price) if want_below else (l.bottom > price)
            if on_right_side and beyond_price:
                pool.append(l)

        if not pool:
            return None

        swings = [l for l in pool if l.type is LevelType.SWING]
        if swings:
            pool = swings

        # Nearest first — the first thing price would reach.
        return min(pool, key=lambda l: l.distance_pips(price))


# ---------------------------------------------------------------- SMT

def check_smt(
    eur: List[Bar], eur_swings: List[Swing],
    comp: List[Bar], comp_swings: List[Swing],
    bias: Direction,
    as_of_index: int,
    comp_inverted: bool = False,
    window: int = 3,
) -> Optional[dict]:
    """SMT divergence. Rulebook §8.

    `comp_inverted=True` for DXY, which moves against EURUSD — so a EURUSD
    lower low should pair with a DXY higher high, and the divergence is DXY
    FAILING to make that higher high.

    Returns a dict when divergence is present, else None.
    """
    if bias is Direction.NONE:
        return None

    want_low = bias is Direction.BULLISH  # bullish SMT forms at a low

    def last_two(swings, is_high):
        got = [s for s in swings
               if s.is_high == is_high and s.confirmed_index <= as_of_index]
        return got[-2:] if len(got) >= 2 else None

    e = last_two(eur_swings, is_high=not want_low)
    if not e:
        return None

    # On DXY the mirrored swing is the opposite side.
    comp_is_high = (not want_low) if not comp_inverted else want_low
    c = last_two(comp_swings, is_high=comp_is_high)
    if not c:
        return None

    if abs(e[-1].index - c[-1].index) > window:
        return None  # not the same move

    if want_low:
        eur_made = e[-1].price < e[-2].price             # EURUSD lower low
        comp_made = (c[-1].price < c[-2].price) if not comp_inverted \
            else (c[-1].price > c[-2].price)             # DXY higher high
    else:
        eur_made = e[-1].price > e[-2].price             # EURUSD higher high
        comp_made = (c[-1].price > c[-2].price) if not comp_inverted \
            else (c[-1].price < c[-2].price)             # DXY lower low

    if eur_made and not comp_made:
        return {
            "divergence": True,
            "direction": bias.value,
            "eur_index": e[-1].index,
            "comp_index": c[-1].index,
            "note": "EURUSD extended, comparison did not follow",
        }
    return None


# ---------------------------------------------------------------- scoring

CHECKS: Dict[str, int] = {
    "weekly_bias":      2,
    "daily_bias":       2,
    "h1_profile":       1,
    "correct_side_eq":  1,
    "target_exists":    1,
    "crt_present":      2,
    "plp_taken":        1,
    "plp_aligned":      2,
    "smt_confirmed":    2,
    "model1_found":     1,
}
MAX_SCORE = sum(CHECKS.values())


@dataclass
class ConfluenceReport:
    """What the dashboard renders. Rulebook §10."""
    results: Dict[str, bool]
    detail: Dict[str, str]
    direction: Direction

    @property
    def score(self) -> int:
        return sum(w for k, w in CHECKS.items() if self.results.get(k))

    @property
    def max_score(self) -> int:
        return MAX_SCORE

    @property
    def met(self) -> List[str]:
        return [k for k in CHECKS if self.results.get(k)]

    @property
    def missing(self) -> List[str]:
        return [k for k in CHECKS if not self.results.get(k)]

    def to_dict(self) -> dict:
        return {
            "direction": self.direction.value,
            "score": self.score,
            "max_score": self.max_score,
            "results": self.results,
            "detail": self.detail,
            "met": self.met,
            "missing": self.missing,
            # Said out loud so nobody reads the number as an edge.
            "disclaimer": "Condition count, not a signal. Weights are untested.",
        }


def build_report(
    direction: Direction,
    weekly_ok: bool = False,
    daily_ok: bool = False,
    h1_ok: bool = False,
    correct_side: bool = False,
    target: Optional[Level] = None,
    crt=None,
    plp: Optional[PLP] = None,
    plp_alignment: Optional[List[Level]] = None,
    smt: Optional[dict] = None,
    model1=None,
) -> ConfluenceReport:
    """Assemble the panel. Every field is a fact, not a judgement."""
    aligned = plp_alignment or []

    results = {
        "weekly_bias": weekly_ok,
        "daily_bias": daily_ok,
        "h1_profile": h1_ok,
        "correct_side_eq": correct_side,
        "target_exists": target is not None,
        "crt_present": crt is not None,
        "plp_taken": plp is not None and plp.is_taken,
        "plp_aligned": len(aligned) > 0,
        "smt_confirmed": smt is not None,
        "model1_found": model1 is not None,
    }

    detail = {
        "target": f"{target.type.value} @ {target.mid:.5f}" if target else "none",
        "crt": f"{crt.timeframe.value} {crt.direction.value}, eq {crt.eq:.5f}"
               if crt else "none",
        "plp": f"{plp.price:.5f} ({'taken' if plp and plp.is_taken else 'resting'})"
               if plp else "none",
        "plp_aligned_with": ", ".join(
            f"{l.timeframe.value} {l.type.value}" for l in aligned) or "nothing",
        "smt": smt["note"] if smt else "none",
        "model1": f"entry {model1.entry:.5f} / stop {model1.stop:.5f} "
                  f"({model1.risk / PIP:.1f} pips)" if model1 else "none",
    }

    return ConfluenceReport(results=results, detail=detail, direction=direction)
