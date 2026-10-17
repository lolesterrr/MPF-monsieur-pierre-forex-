"""Swing highs and lows. Rulebook §1.

The important part is `confirmed_index`. A swing at bar i is not knowable until
bar i+n closes. Every downstream rule must use confirmation time, or the whole
thing looks psychic in backtest and fails live.
"""

from __future__ import annotations

from typing import List

from ..core.types import Bar, Direction, Swing


def find_swings(bars: List[Bar], n: int = 2) -> List[Swing]:
    """Return every confirmed swing in `bars`.

    A swing high at i needs high[i] strictly greater than the n bars either side.
    Mirror for lows. Ties are rejected — a flat double-top is not a swing.
    """
    if n < 1:
        raise ValueError("n must be >= 1")

    swings: List[Swing] = []

    for i in range(n, len(bars) - n):
        left = bars[i - n:i]
        right = bars[i + 1:i + 1 + n]
        confirmed = i + n

        if all(bars[i].high > b.high for b in left) and \
           all(bars[i].high > b.high for b in right):
            swings.append(Swing(
                index=i,
                ts=bars[i].ts,
                price=bars[i].high,
                direction=Direction.BEARISH,  # a high is bearish-side liquidity
                confirmed_index=confirmed,
                confirmed_ts=bars[confirmed].ts,
            ))

        if all(bars[i].low < b.low for b in left) and \
           all(bars[i].low < b.low for b in right):
            swings.append(Swing(
                index=i,
                ts=bars[i].ts,
                price=bars[i].low,
                direction=Direction.BULLISH,
                confirmed_index=confirmed,
                confirmed_ts=bars[confirmed].ts,
            ))

    return swings


def mark_sweeps(bars: List[Bar], swings: List[Swing]) -> List[Swing]:
    """Fill in swept_index / mitigated_index.

    swept     = a later bar's wick went beyond the swing
    mitigated = a later bar's *close* went beyond it

    Scanning starts at confirmed_index + 1, never at the swing's own index.
    """
    for s in swings:
        start = s.confirmed_index + 1
        for j in range(start, len(bars)):
            bar = bars[j]
            if s.is_high:
                if s.swept_index is None and bar.high > s.price:
                    s.swept_index = j
                if s.mitigated_index is None and bar.close > s.price:
                    s.mitigated_index = j
                    break
            else:
                if s.swept_index is None and bar.low < s.price:
                    s.swept_index = j
                if s.mitigated_index is None and bar.close < s.price:
                    s.mitigated_index = j
                    break
    return swings


def unswept_swings(swings: List[Swing], as_of_index: int,
                   direction: Direction | None = None) -> List[Swing]:
    """Swings that were confirmed by `as_of_index` and not yet swept by then.

    This is the PLP candidate pool (Rulebook §6).
    """
    out = []
    for s in swings:
        if s.confirmed_index > as_of_index:
            continue  # not knowable yet
        if s.swept_index is not None and s.swept_index <= as_of_index:
            continue  # already taken
        if direction is not None and s.direction is not direction:
            continue
        out.append(s)
    return out
