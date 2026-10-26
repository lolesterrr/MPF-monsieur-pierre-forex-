"""Walk history and collect every setup the rules produce.

This is where the confluence score stops being decoration and starts being
testable: each setup records which conditions were met, so you can ask
"did the ones with SMT actually do better?" instead of assuming.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core.bias import daily_bias, h1_profile, weekly_bias
from ..core.confluence import LevelPool, mark_plp
from ..core.entry import (
    MIN_RR_T1, STOP_BUFFER_PIPS, arm_time, c3_end, c3_tradeable,
    entry_timeframe, fresh_pd_array, pd_array_below, rr, stab_index,
    sweep_index,
)
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
    entry_mode: str = ""        # "close" | "limit"
    entry_tf: str = ""
    crt_tf: str = ""
    armed_ts: int = 0           # when the Model #1 trigger candle closed
    via: str = ""               # "plp" | "pd_array"

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
    diag: Optional[Dict[str, int]] = None,
    trade_from_ts: Optional[int] = None,
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
    for tf in (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1):
        b = tfs[tf]
        if len(b) >= 25:
            pool.add(update_level_status(b, find_fvgs(b, tf)))
            pool.add(update_level_status(b, find_order_block_candidates(b, tf)))
            pool.add(_swing_levels(b, tf))

    pip = fills.pip
    funnel = diag if diag is not None else {}

    def drop(reason: str) -> None:
        funnel[reason] = funnel.get(reason, 0) + 1

    for crt_tf in (Timeframe.D1, Timeframe.H4):
        tf_bars = tfs[crt_tf]
        plp_tf = crt_tf.plp_tier or Timeframe.H1
        plp_bars = tfs[plp_tf]
        plp_sw = mark_sweeps(plp_bars, find_swings(plp_bars))

        for crt in find_crts(tf_bars, crt_tf):
            # A CRT whose C3 has not printed is the live one — real and
            # tradeable, but there is no C3 bar to replay, so a historical
            # run has nothing to measure. Skipping it is correct here and is
            # NOT the same as hiding it from the dashboard.
            c3 = crt.c3_index
            if c3 < 2 or c3 >= len(tf_bars):
                continue
            if trade_from_ts is not None and crt.c3_ts < trade_from_ts:
                continue   # history only: read, never traded
            drop("crts")
            c3_ts = crt.c3_ts
            if not c3_tradeable(crt_tf, c3_ts):
                drop("x_off_hours_4h")
                continue
            arm_ts = arm_time(crt_tf, c3_ts)
            end_ts = c3_end(crt_tf, c3_ts)

            # --- bias as it stood when C3 opened (closed bars only)
            wk_i = _last_closed(wk, c3_ts)
            dy_i = _last_closed(dy, c3_ts)
            if wk_i < 1 or dy_i < 1:
                drop("x_warmup")
                continue

            wb = weekly_bias(wk[wk_i - 1], wk[wk_i], wk_i)
            db = daily_bias(dy[dy_i - 1], dy[dy_i], dy_i)
            bias = wb.direction if wb.tradeable else db.direction
            if bias is Direction.NONE or crt.direction is not bias:
                drop("x_bias")
                continue

            seen = [s for s in h1_sw if s.confirmed_ts and s.confirmed_ts <= c3_ts]
            hp = h1_profile([s.price for s in seen if s.is_high],
                            [s.price for s in seen if not s.is_high])

            # --- PLP on the tier below, marked against C3's mark (00:00 NY
            # for a daily C3), and it has to be taken inside C3.
            as_of = _last_closed(plp_bars, arm_ts)
            if as_of < 5:
                drop("x_warmup")
                continue
            plp = mark_plp(plp_bars, plp_sw, bias, as_of, plp_tf)
            via, pd_level, aligned = "plp", None, []
            if plp:
                if not plp.is_taken or plp_bars[plp.taken_index].ts >= end_ts:
                    drop("x_plp_not_taken_in_c3")
                    continue
                aligned = pool.near(plp.price, tolerance_pips=5.0,
                                    exclude_tf=plp_tf)
                pd = pd_array_below(pool.levels, plp.price, bias, arm_ts, pip) \
                    if crt_tf is Timeframe.D1 else None
                etf = entry_timeframe(crt_tf, pd is not None)
                ebars = tfs[etf]
                si = sweep_index(ebars, plp.price, bias,
                                 max(arm_ts, plp_bars[plp.taken_index].ts))
            else:
                # No PLP: the author enters off a fresh PD array instead
                # (4H for a daily CRT, 1H/15M for a 4H CRT).
                pd_level = fresh_pd_array(pool.levels, tfs, crt_tf, bias, arm_ts)
                if pd_level is None:
                    drop("x_no_plp")
                    continue
                via = "pd_array"
                etf = entry_timeframe(crt_tf, False)
                ebars = tfs[etf]
                si = stab_index(ebars, pd_level, bias, arm_ts, end_ts)
                if si is None:
                    drop("x_pd_not_reached_in_c3")
                    continue

            # --- Model #1 on the entry timeframe, inside C3
            if si is None:
                drop("x_no_model1")
                continue
            m1 = find_model1(ebars, bias, si, etf,
                             stop_buffer=STOP_BUFFER_PIPS * pip, until_ts=end_ts)
            if not m1:
                drop("x_no_model1")
                continue

            t1, t2 = crt.eq, crt.opposing
            # Nothing happens before the trigger candle has CLOSED.
            armed = _index_at_or_after(bars_m5, m1.armed_ts)
            if armed is None:
                drop("x_warmup")
                continue

            if rr(m1.trigger_close, m1.stop, t1, bias) >= MIN_RR_T1:
                mode, entry, start = "close", m1.trigger_close, armed
            elif rr(m1.entry, m1.stop, t1, bias) >= MIN_RR_T1:
                mode, entry = "limit", m1.entry
                start = _limit_fill(bars_m5, armed, entry, t1, bias, end_ts)
                if start is None:
                    drop("x_limit_unfilled")
                    continue
            else:
                drop("x_rr_below_2")
                continue

            t = Trade(direction=bias, entry_ts=bars_m5[start].ts,
                      entry=entry, stop=m1.stop, t1=t1, t2=t2)
            if rr(entry, t.stop, t2, bias) <= 0 or t.risk <= 0:
                drop("x_rr_below_2")
                continue

            simulate(t, bars_m5, start, fills)
            drop("traded")

            setups.append(Setup(
                model="crt", direction=bias, trade=t, entry_index=start,
                entry_mode=mode, entry_tf=etf.value, crt_tf=crt_tf.value,
                armed_ts=m1.armed_ts, via=via,
                conditions={
                    "weekly_bias": wb.tradeable and wb.direction is bias,
                    "daily_bias": db.tradeable and db.direction is bias,
                    "h1_profile": hp.tradeable and hp.direction is bias,
                    "correct_side_eq": True,
                    "target_exists": True,
                    "crt_present": True,
                    "plp_taken": via == "plp",
                    "pd_array_entry": via == "pd_array",
                    # "preferably formed during C2"
                    "pd_in_c2": bool(pd_level) and pd_level.created_ts >= crt.c2_ts,
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
    i = bisect_right(bars, ts, key=lambda b: b.ts)
    return i if i < len(bars) else None


def _index_at_or_after(bars: List[Bar], ts: int) -> Optional[int]:
    i = bisect_left(bars, ts, key=lambda b: b.ts)
    return i if i < len(bars) else None


def _limit_fill(bars: List[Bar], start: int, entry: float, t1: float,
                bias: Direction, expires_ts: int) -> Optional[int]:
    """Index of the 5m bar where a resting limit fills, or None.

    Cancelled if price reaches T1 first (the move left without you) or C3
    closes unfilled. A bar that touches both T1 and the entry is treated as
    a fill — the worse assumption, since the simulation then starts on it
    and applies stop-first.
    """
    long = bias is Direction.BULLISH
    for i in range(start, len(bars)):
        b = bars[i]
        if b.ts >= expires_ts:
            return None
        touched = b.low <= entry if long else b.high >= entry
        if touched:
            return i
        if (b.high >= t1) if long else (b.low <= t1):
            return None
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

    ftr: Dict[str, int] = {}
    fte: Dict[str, int] = {}
    tr = find_setups(train_bars, anchor_hour_utc, fills, diag=ftr)
    # The test period reads the training candles as HISTORY (swings, levels,
    # bias), and only trades CRTs whose C3 opens inside it. Running it on its
    # own lost its first weeks to warm-up. Train never sees test candles.
    te = find_setups(bars_m5, anchor_hour_utc, fills, diag=fte,
                     trade_from_ts=test_bars[0].ts if test_bars else None)

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
    # Where CRTs fell out of the chain. Without this a low trade count reads
    # as "the strategy rarely fires" when it may be one rule eating them all.
    out["funnel"] = {"train": ftr, "test": fte}
    out["entry_via"] = {
        v: sum(1 for s in te if s.via == v) for v in ("plp", "pd_array")}
    out["entry_modes"] = {
        m: sum(1 for s in te if s.entry_mode == m) for m in ("close", "limit")}
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
