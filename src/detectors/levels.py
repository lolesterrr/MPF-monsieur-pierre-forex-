"""FVGs and order block candidates. Rulebook §2.1, §2.2."""

from __future__ import annotations

from typing import List

from ..core.types import (
    Bar, Direction, Level, LevelStatus, LevelType, Timeframe,
)


def find_fvgs(bars: List[Bar], tf: Timeframe,
              min_size_pips: float = 0.0) -> List[Level]:
    """Three-bar wick gap. Rulebook §2.1.

    Bullish: low[i] > high[i-2]  — gap left below
    Bearish: high[i] < low[i-2]  — gap left above

    Detected at bar i, which is correct: all three bars have closed.
    """
    out: List[Level] = []

    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]

        if c.low > a.high:
            size = (c.low - a.high) / 0.0001
            if size >= min_size_pips:
                out.append(Level(
                    type=LevelType.FVG, direction=Direction.BULLISH,
                    top=c.low, bottom=a.high,
                    created_ts=c.ts, created_index=i, timeframe=tf,
                    meta={"size_pips": round(size, 1)},
                ))

        elif c.high < a.low:
            size = (a.low - c.high) / 0.0001
            if size >= min_size_pips:
                out.append(Level(
                    type=LevelType.FVG, direction=Direction.BEARISH,
                    top=a.low, bottom=c.high,
                    created_ts=c.ts, created_index=i, timeframe=tf,
                    meta={"size_pips": round(size, 1)},
                ))

    return out


def _atr_simple(bars: List[Bar], end: int, period: int = 20) -> float | None:
    """Mean bar range over the `period` bars ENDING at end-1 (exclusive of end)."""
    start = end - period
    if start < 0:
        return None
    window = bars[start:end]
    return sum(b.range for b in window) / len(window)


def find_order_block_candidates(
    bars: List[Bar],
    tf: Timeframe,
    displacement_mult: float = 1.5,
    atr_period: int = 20,
    max_lookback: int = 10,
) -> List[Level]:
    """Cast a wide net. Rulebook §2.2 — Isaiah filters these by eye.

    1. Bar i is a displacement bar if its range >= mult * ATR20(prior bars)
    2. Walk back up to `max_lookback` bars for the nearest opposite-close bar
    3. Zone = that bar's full high-low
    """
    out: List[Level] = []

    for i in range(atr_period, len(bars)):
        atr = _atr_simple(bars, i, atr_period)
        if not atr or atr <= 0:
            continue

        disp = bars[i]
        if disp.range < displacement_mult * atr:
            continue

        if disp.is_down:
            want_up, direction = True, Direction.BEARISH
        elif disp.is_up:
            want_up, direction = False, Direction.BULLISH
        else:
            continue  # doji displacement — no directional read

        for back in range(1, max_lookback + 1):
            j = i - back
            if j < 0:
                break
            cand = bars[j]
            if (cand.is_up if want_up else cand.is_down):
                out.append(Level(
                    type=LevelType.ORDER_BLOCK, direction=direction,
                    top=cand.high, bottom=cand.low,
                    created_ts=cand.ts, created_index=j, timeframe=tf,
                    meta={
                        "displacement_index": i,
                        "displacement_ratio": round(disp.range / atr, 2),
                        "bars_back": back,
                        # UI sets this; None = you haven't judged it yet
                        "accepted": None,
                    },
                ))
                break

    return _dedupe(out)


def _dedupe(levels: List[Level]) -> List[Level]:
    """Two displacement bars can nominate the same origin candle. Keep the first."""
    seen, out = set(), []
    for lv in levels:
        key = (lv.type, lv.created_index, lv.direction)
        if key not in seen:
            seen.add(key)
            out.append(lv)
    return out


def update_level_status(bars: List[Bar], levels: List[Level]) -> List[Level]:
    """Rulebook §2.1/§2.2 invalidation.

    A wick through a zone marks it SWEPT and nothing more — that is the behaviour
    the whole strategy is built on. Only a CLOSE through it kills the level.
    """
    for lv in levels:
        for j in range(lv.created_index + 1, len(bars)):
            bar = bars[j]

            if lv.direction is Direction.BULLISH:
                if bar.low < lv.bottom and lv.status is LevelStatus.VALID:
                    lv.status = LevelStatus.SWEPT
                if bar.close < lv.bottom:
                    lv.status = LevelStatus.INVALIDATED
                    lv.invalidated_ts = bar.ts
                    break
            else:
                if bar.high > lv.top and lv.status is LevelStatus.VALID:
                    lv.status = LevelStatus.SWEPT
                if bar.close > lv.top:
                    lv.status = LevelStatus.INVALIDATED
                    lv.invalidated_ts = bar.ts
                    break

    return levels
