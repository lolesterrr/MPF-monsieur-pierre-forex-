"""The C3 entry, as the strategy's author trades it. Rulebook §7.

Shared by the live dashboard (pipeline) and the backtest (replay) so the two
can never describe different trades.

The chain, bullish (bearish mirrors):

1. C3's open is the clock. A daily C3 is marked at 00:00 New York (the daily
   candle itself opens at 17:00, but the author marks midnight). A 4H C3 is
   marked at its own open, and only the 01:00, 05:00, 09:00 and 13:00 NY 4H
   candles are traded as C3.
2. The PLP is marked on the tier below (1H for a daily CRT, 15M for a 4H),
   from swings that existed before that mark. See confluence.mark_plp.
3. Once the PLP is taken, Model #1 runs on the entry timeframe:
     daily CRT -> 15M, or 5M when a 1H PD array rests just below the PLP
     4H CRT    -> 5M (the author also uses 3M; MPF's data starts at 5M)
4. Stop: below the Model #1 candle's low, plus 1-3 pips for spread and stray
   wicks. MPF uses 2.
5. RR to T1 must be at least 2. If entering at the trigger candle's close
   already gives 2R, take it there. Otherwise place a limit back at the Model
   #1 level, and take that only if IT gives 2R.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from .types import Bar, Direction, Level, LevelType, Timeframe

NY = ZoneInfo("America/New_York")

#: 4H candles the author trades as C3 (New York open hours). 13:00 is "only
#: ever traded as the 3rd candle"; 01:00 as C3 means its C2 was 21:00, which
#: he lists as tradeable C2s via 01:00/05:00/09:00.
C3_HOURS_4H = {1, 5, 9, 13}

STOP_BUFFER_PIPS = 2.0
MIN_RR_T1 = 2.0
#: How far below the PLP a 1H PD array can rest and still "sit below" it.
PD_ARRAY_REACH_PIPS = 20.0


def ny_hour(ts: int) -> int:
    return datetime.fromtimestamp(ts, NY).hour


def c3_tradeable(crt_tf: Timeframe, c3_ts: int) -> bool:
    """Is this C3 one the author would trade at all?"""
    if crt_tf is Timeframe.H4:
        return ny_hour(c3_ts) in C3_HOURS_4H
    return True


def arm_time(crt_tf: Timeframe, c3_ts: int) -> int:
    """The moment the PLP is marked against: 00:00 NY for a daily C3."""
    if crt_tf is Timeframe.D1:
        return c3_ts + 7 * 3600      # 17:00 NY open + 7h = midnight NY
    return c3_ts


def c3_end(crt_tf: Timeframe, c3_ts: int) -> int:
    return c3_ts + crt_tf.seconds


def pd_array_below(levels: List[Level], plp_price: float, bias: Direction,
                   before_ts: int, pip: float) -> Optional[Level]:
    """A 1H FVG or order block resting just beyond the PLP, if there is one.

    Bullish: its top sits at or below the PLP and within PD_ARRAY_REACH_PIPS
    of it. Only levels that existed before `before_ts` count.
    """
    reach = PD_ARRAY_REACH_PIPS * pip
    best = None
    for l in levels:
        if l.timeframe is not Timeframe.H1 or l.direction is not bias:
            continue
        if l.type not in (LevelType.FVG, LevelType.ORDER_BLOCK):
            continue
        if l.created_ts >= before_ts:
            continue
        if bias is Direction.BULLISH:
            ok = plp_price - reach <= l.top <= plp_price
        else:
            ok = plp_price <= l.bottom <= plp_price + reach
        if ok and (best is None or l.created_ts > best.created_ts):
            best = l
    return best


def entry_timeframe(crt_tf: Timeframe, has_pd_array: bool) -> Timeframe:
    if crt_tf is Timeframe.H4:
        return Timeframe.M5
    return Timeframe.M5 if has_pd_array else Timeframe.M15


def rr(entry: float, stop: float, target: float, bias: Direction) -> float:
    risk = (entry - stop) if bias is Direction.BULLISH else (stop - entry)
    reward = (target - entry) if bias is Direction.BULLISH else (entry - target)
    # Rounded so a level placed at exactly 2R is not rejected as 1.9999999.
    return round(reward / risk, 6) if risk > 0 else 0.0


def sweep_index(bars: List[Bar], price: float, bias: Direction,
                from_ts: int) -> Optional[int]:
    """First bar at/after `from_ts` that trades through the PLP."""
    for i in range(bisect_left(bars, from_ts, key=lambda b: b.ts), len(bars)):
        b = bars[i]
        if (b.low < price) if bias is Direction.BULLISH else (b.high > price):
            return i
    return None
