"""Walk history and collect every setup the rules produce.

This is where the confluence score stops being decoration and starts being
testable: each setup records which conditions were met, so you can ask
"did the ones with SMT actually do better?" instead of assuming.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core.bias import daily_bias, h1_profile, weekly_bias
from ..core.confluence import LevelPool, mark_plp
from ..core.sessions import build_asian_ranges, classify_break
from ..core.types import Bar, Direction, Level, LevelType, Timeframe
from ..data.feed import resample
from ..detectors.crt import find_crts
from ..detectors.levels import find_fvgs, find_order_block_candidates, update_level_status
from ..detectors.model1 import find_model1
from ..detectors.swings import find_swings, mark_sweeps
from .engine import Fills, Stats, Trade, simulate, split, verdict


@dataclass
class Setup:
    """One candidate trade plus the conditions that were true when it formed."""
    model: str                  # "crt" | "asia_a" | "asia_b"
    direction: Direction
    trade: Trade
    conditions: Dict[str, bool]
    entry_index: int

    @property
    def score(self) -> int:
        from ..core.confluence import CHECKS
        return sum(w for k, w in CHECKS.items() if self.conditions.get(k))


def _swing_levels(bars: List[Bar], tf: Timeframe) -> List[Level]:
    out = []
    for s in mark_sweeps(bars, find_swings(bars)):
        if s.mitigated_index is not None:
            continue
        out.append(Level(
            type=LevelType.SWING,
            direction=Direction.BEARISH if s.is_high else Direction.BULLISH,
            top=s.price, bottom=s.price,
            created_ts=s.ts, created_index=s.index, timeframe=tf))
    return out


def find_setups(
    bars_m5: List[Bar],
    anchor_hour_utc: int = 21,
    fills: Optional[Fills] = None,
) -> List[Setup]:
    """Model 1 (CRT) setups across the dataset.

    Walks CRTs in order. For each, marks the PLP on the lower tier using only
    bars that existed when C3 opened, then looks for the Model #1 trigger.
    """
    fills = fills or Fills()
    setups: List[Setup] = []

    tfs = {
        Timeframe.M5: bars_m5,
        Timeframe.M15: resample(bars_m5, Timeframe.M15),
        Timeframe.H1: resample(bars_m5, Timeframe.H1),
        Timeframe.H4: resample(bars_m5, Timeframe.H4),
        Timeframe.D1: resample(bars_m5, Timeframe.D1, anchor_hour_utc),
        Timeframe.W1: resample(bars_m5, Timeframe.W1, anchor_hour_utc),
    }
    if len(tfs[Timeframe.W1]) < 3 or len(tfs[Timeframe.D1]) < 5:
        return []

    wk, dy, h1 = tfs[Timeframe.W1], tfs[Timeframe.D1], tfs[Timeframe.H1]
    h1_sw = mark_sweeps(h1, find_swings(h1))

    # Pre-build the level pool once per timeframe; status is updated as we go.
    pool = LevelPool()
    for tf in (Timeframe.H1, Timeframe.H4, Timeframe.D1):
        b = tfs[tf]
        if len(b) >= 25:
            pool.add(update_level_status(b, find_fvgs(b, tf)))
            pool.add(update_level_status(b, find_order_block_candidates(b, tf)))
            pool.add(_swing_levels(b, tf))

    for crt_tf in (Timeframe.D1, Timeframe.H4):
        tf_bars = tfs[crt_tf]
        plp_tf = crt_tf.plp_tier or Timeframe.H1
        plp_bars = tfs[plp_tf]
        plp_sw = mark_sweeps(plp_bars, find_swings(plp_bars))

        for crt in find_crts(tf_bars, crt_tf):
            # --- bias as it stood when C3 opened (closed bars only)
            c3 = crt.c3_index
            if c3 < 2 or c3 >= len(tf_bars):
                continue
            c3_ts = tf_bars[c3].ts

            wk_i = _last_closed(wk, c3_ts)
            dy_i = _last_closed(dy, c3_ts)
            if wk_i < 1 or dy_i < 1:
                continue

            wb = weekly_bias(wk[wk_i - 1], wk[wk_i], wk_i)
            db = daily_bias(dy[dy_i - 1], dy[dy_i], dy_i)
            bias = wb.direction if wb.tradeable else db.direction
            if bias is Direction.NONE or crt.direction is not bias:
                continue

            # --- 1H profile using only confirmed swings before C3
            seen = [s for s in h1_sw if s.confirmed_ts and s.confirmed_ts <= c3_ts]
            hp = h1_profile([s.price for s in seen if s.is_high],
                            [s.price for s in seen if not s.is_high])

            # --- PLP on the lower tier
            as_of = _last_closed(plp_bars, c3_ts)
            if as_of < 5:
                continue
            plp = mark_plp(plp_bars, plp_sw, bias, as_of, plp_tf)
            if not plp or not plp.is_taken:
                continue

            aligned = pool.near(plp.price, tolerance_pips=5.0, exclude_tf=plp_tf)

            # --- Model #1 after the PLP was taken
            m1 = find_model1(plp_bars, bias, plp.taken_index, plp_tf)
            if not m1:
                continue

            # --- the trade, with the doc's exits
            t = Trade(
                direction=bias,
                entry_ts=plp_bars[m1.trigger_index].ts,
                entry=m1.entry, stop=m1.stop,
                t1=crt.eq, t2=crt.opposing,
            )
            # A target already behind price is not a target.
            if bias is Direction.BULLISH and not (t.t1 > t.entry and t.t2 > t.entry):
                continue
            if bias is Direction.BEARISH and not (t.t1 < t.entry and t.t2 < t.entry):
                continue
            if t.risk <= 0:
                continue

            start = _index_after(bars_m5, t.entry_ts)
            if start is None:
                continue
            simulate(t, bars_m5, start, fills)

            setups.append(Setup(
                model="crt", direction=bias, trade=t, entry_index=start,
                conditions={
                    "weekly_bias": wb.tradeable and wb.direction is bias,
                    "daily_bias": db.tradeable and db.direction is bias,
                    "h1_profile": hp.tradeable and hp.direction is bias,
                    "correct_side_eq": True,
                    "target_exists": True,
                    "crt_present": True,
                    "plp_taken": True,
                    "plp_aligned": len(aligned) > 0,
                    "smt_confirmed": False,   # needs a second symbol
                    "model1_found": True,
                },
            ))

    setups.sort(key=lambda s: s.trade.entry_ts)
    return setups


def _last_closed(bars: List[Bar], ts: int) -> int:
    """Index of the newest bar that had CLOSED by `ts`. The no-lookahead helper."""
    lo, hi = 0, len(bars) - 1
    out = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if bars[mid].ts < ts:
            out, lo = mid, mid + 1
        else:
            hi = mid - 1
    return out


def _index_after(bars: List[Bar], ts: int) -> Optional[int]:
    for i, b in enumerate(bars):
        if b.ts > ts:
            return i
    return None


# ------------------------------------------------------------------ report

def evaluate(
    bars_m5: List[Bar],
    anchor_hour_utc: int = 21,
    fills: Optional[Fills] = None,
    train_frac: float = 0.6,
    min_score: int = 0,
) -> dict:
    """Run the replay on a train/test split and report honestly.

    The split is chronological and not optional. Reporting one number over the
    whole dataset is how every strategy looks profitable.
    """
    fills = fills or Fills()
    train_bars, test_bars = split(bars_m5, train_frac)

    tr = find_setups(train_bars, anchor_hour_utc, fills)
    te = find_setups(test_bars, anchor_hour_utc, fills)

    tr = [s for s in tr if s.score >= min_score]
    te = [s for s in te if s.score >= min_score]

    st, ss = Stats(), Stats()
    for s in tr:
        st.add(s.trade)
    for s in te:
        ss.add(s.trade)

    out = verdict(st, ss)
    out["min_score"] = min_score
    out["fills"] = {"spread_pips": fills.spread_pips,
                    "slippage_pips": fills.slippage_pips}
    out["period"] = {
        "train": _range(train_bars),
        "test": _range(test_bars),
    }
    out["by_condition"] = _condition_breakdown(te)
    return out


def _range(bars: List[Bar]) -> dict:
    return {"from": bars[0].ts, "to": bars[-1].ts, "bars": len(bars)} if bars else {}


def _condition_breakdown(setups: List[Setup]) -> dict:
    """Expectancy with and without each condition, out-of-sample.

    This is the only way to find out whether a confluence check earns its
    weight. Read it with suspicion: with ten conditions and few trades,
    something will look good by chance alone.
    """
    from ..core.confluence import CHECKS
    out = {}
    for k in CHECKS:
        with_, without = Stats(), Stats()
        for s in setups:
            (with_ if s.conditions.get(k) else without).add(s.trade)
        out[k] = {
            "with": {"n": with_.n, "expectancy_r": round(with_.expectancy, 3)},
            "without": {"n": without.n, "expectancy_r": round(without.expectancy, 3)},
        }
    out["_warning"] = (
        "Ten conditions tested at once. Expect one or two to look strong purely "
        "by chance — that is the multiple-comparisons problem, not a discovery. "
        "Treat anything here as a hypothesis to test on fresh data.")
    return out
