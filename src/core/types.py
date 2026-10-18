"""Shared data types. Everything else in the repo speaks these.

Rulebook: rules/RULEBOOK.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


PIP = 0.0001  # EURUSD and most majors — the default

#: Pip size per instrument. Getting this wrong silently corrupts every risk
#: number: gold moves in 0.01, so a 4.5-point stop read as EURUSD pips comes
#: out as 45000 instead of 450.
PIP_SIZES = {
    "XAUUSD": 0.01, "GOLD": 0.01,
    "XAGUSD": 0.001, "SILVER": 0.001,
    "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01,   # JPY quotes to 2dp
    "NAS100": 1.0, "SPX500": 1.0, "US30": 1.0,
    "NQ": 0.25, "ES": 0.25, "YM": 1.0, "RTY": 0.1,     # CME tick sizes
    "OIL": 0.01, "DXY": 0.001, "USDX": 0.001,
}


def pip_size(symbol: str | None) -> float:
    """Pip size for an instrument, defaulting to the major-pair 0.0001."""
    return PIP_SIZES.get((symbol or "").upper(), PIP)


class Direction(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"

    @property
    def opposite(self) -> "Direction":
        if self is Direction.BULLISH:
            return Direction.BEARISH
        if self is Direction.BEARISH:
            return Direction.BULLISH
        return Direction.NONE


class LevelType(Enum):
    FVG = "fvg"
    ORDER_BLOCK = "order_block"
    SWING = "swing"
    ASIA_HIGH = "asia_high"
    ASIA_LOW = "asia_low"
    ASIA_50 = "asia_50"
    CRT_50 = "crt_50"
    CRT_RANGE = "crt_range"


class LevelStatus(Enum):
    VALID = "valid"
    SWEPT = "swept"          # wicked through, not closed through
    INVALIDATED = "invalidated"  # closed through — dead


class Timeframe(Enum):
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    @property
    def plp_tier(self) -> Optional["Timeframe"]:
        """Rulebook 5.3 — the tier one step down for PLP marking."""
        return {Timeframe.D1: Timeframe.H1, Timeframe.H4: Timeframe.M15}.get(self)

    @property
    def seconds(self) -> int:
        """Nominal bar length. Nominal because the market shuts at weekends,
        so the NEXT bar is not always this far ahead — see
        market_hours.next_open, which is what you want for projecting forward.
        """
        return {
            Timeframe.M5: 300, Timeframe.M15: 900, Timeframe.H1: 3600,
            Timeframe.H4: 14400, Timeframe.D1: 86400, Timeframe.W1: 604800,
        }[self]


@dataclass(frozen=True)
class Bar:
    """One OHLC bar. `ts` is UTC epoch seconds."""
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_up(self) -> bool:
        return self.close > self.open

    @property
    def is_down(self) -> bool:
        return self.close < self.open

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)


@dataclass
class Level:
    """A zone or price the strategy cares about. Rulebook §2."""
    type: LevelType
    direction: Direction
    top: float
    bottom: float
    created_ts: int
    created_index: int
    timeframe: Timeframe
    status: LevelStatus = LevelStatus.VALID
    invalidated_ts: Optional[int] = None
    meta: dict = field(default_factory=dict)

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def is_zero_width(self) -> bool:
        return self.top == self.bottom

    @property
    def id(self) -> str:
        """Stable id so UI accept/reject choices survive a reload."""
        return f"{self.timeframe.value}:{self.type.value}:{self.created_ts}:{self.bottom:.5f}"

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def distance_pips(self, price: float, pip: float = PIP) -> float:
        """Distance from price to the nearest edge of the zone, in pips.

        `pip` defaults to the major-pair size. Pass pip_size(symbol) when the
        instrument is known — detectors run one instrument at a time, so the
        default is right for EURUSD and wrong for gold.
        """
        if self.contains(price):
            return 0.0
        gap = self.bottom - price if price < self.bottom else price - self.top
        return gap / pip


@dataclass
class Swing:
    """Rulebook §1. `confirmed_index` is when it became *usable*, not when it formed."""
    index: int
    ts: int
    price: float
    direction: Direction  # BULLISH = swing low, BEARISH = swing high
    confirmed_index: int
    confirmed_ts: int
    swept_index: Optional[int] = None
    mitigated_index: Optional[int] = None

    @property
    def is_high(self) -> bool:
        return self.direction is Direction.BEARISH

    @property
    def is_swept(self) -> bool:
        return self.swept_index is not None
