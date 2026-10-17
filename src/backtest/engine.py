"""Bar-by-bar replay. Rulebook §12 (v2).

The question this answers: when the conditions in the strategy doc line up,
does price actually reach the documented targets more often than it doesn't?

Design rules, because this is the part that lies to people:

1. **No lookahead.** The replay feeds bars in one at a time. A detector that
   peeks forward is a bug, and §0 of the rulebook is tested for it.
2. **Fills are pessimistic.** Spread is charged on entry and exit. When a bar's
   range contains both the stop and the target, the STOP is assumed first.
   Without that assumption every backtest of a range strategy looks great.
3. **Train/test split is default, not optional.** `evaluate()` refuses to
   report a tuned-on result as if it were out-of-sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.types import PIP, Bar, Direction, pip_size


@dataclass
class Fills:
    """What you actually pay. Defaults are deliberately unkind.

    EURUSD typical spread is well under a pip on a good broker, but a 27-pip
    stop with 1 pip of cost each way is ~7% of the risk. It matters.
    """
    spread_pips: float = 1.0
    slippage_pips: float = 0.2
    #: instrument whose pip size applies; None means major-pair 0.0001
    symbol: Optional[str] = None

    @property
    def pip(self) -> float:
        return pip_size(self.symbol)

    @property
    def cost(self) -> float:
        return (self.spread_pips + self.slippage_pips) * self.pip


@dataclass
class Trade:
    direction: Direction
    entry_ts: int
    entry: float
    stop: float
    t1: float
    t2: float
    #: fraction closed at t1; the rest runs to t2 (strategy doc: 60/40)
    t1_fraction: float = 0.6

    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    outcome: str = "open"          # t1_only | t2 | stopped | timeout | open
    r_multiple: float = 0.0
    bars_held: int = 0
    hit_t1: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def risk_pips(self) -> float:
        return self.risk / PIP


def simulate(
    trade: Trade,
    bars: List[Bar],
    start_index: int,
    fills: Fills,
    max_bars: int = 288,          # 24h of 5m bars
) -> Trade:
    """Walk a single trade forward. Rulebook §5.2, §9.2 — 60% at T1, 40% at T2,
    and **no breakeven**, because the doc says price tends to retrace through
    the entry.

    The stop-first rule: if one bar's high and low span both levels, we cannot
    know from OHLC which came first, so we assume the worse one.
    """
    long = trade.direction is Direction.BULLISH
    cost = fills.cost
    risk = trade.risk
    if risk <= 0:
        trade.outcome = "invalid"
        return trade

    realised = 0.0
    remaining = 1.0

    for i in range(start_index, min(start_index + max_bars, len(bars))):
        bar = bars[i]
        trade.bars_held = i - start_index + 1

        hit_stop = bar.low <= trade.stop if long else bar.high >= trade.stop
        hit_t1 = bar.high >= trade.t1 if long else bar.low <= trade.t1
        hit_t2 = bar.high >= trade.t2 if long else bar.low <= trade.t2

        # Pessimistic ordering.
        if hit_stop:
            px = trade.stop - cost if long else trade.stop + cost
            realised += remaining * ((px - trade.entry) if long else (trade.entry - px))
            trade.exit_ts, trade.exit_price = bar.ts, px
            trade.outcome = "t1_then_stopped" if trade.hit_t1 else "stopped"
            trade.r_multiple = realised / risk
            return trade

        if hit_t1 and not trade.hit_t1:
            px = trade.t1 - cost if long else trade.t1 + cost
            realised += trade.t1_fraction * ((px - trade.entry) if long else (trade.entry - px))
            remaining -= trade.t1_fraction
            trade.hit_t1 = True

        if hit_t2 and trade.hit_t1:
            px = trade.t2 - cost if long else trade.t2 + cost
            realised += remaining * ((px - trade.entry) if long else (trade.entry - px))
            trade.exit_ts, trade.exit_price = bar.ts, px
            trade.outcome = "t2"
            trade.r_multiple = realised / risk
            return trade

    # Ran out of bars. Mark to market rather than pretending it closed flat.
    last = bars[min(start_index + max_bars, len(bars)) - 1]
    px = last.close - cost if long else last.close + cost
    realised += remaining * ((px - trade.entry) if long else (trade.entry - px))
    trade.exit_ts, trade.exit_price = last.ts, px
    trade.outcome = "timeout"
    trade.r_multiple = realised / risk
    return trade


# ------------------------------------------------------------------ stats

@dataclass
class Stats:
    n: int = 0
    wins: int = 0
    losses: int = 0
    total_r: float = 0.0
    r_values: List[float] = field(default_factory=list)
    by_outcome: Dict[str, int] = field(default_factory=dict)

    def add(self, t: Trade) -> None:
        self.n += 1
        self.total_r += t.r_multiple
        self.r_values.append(t.r_multiple)
        self.by_outcome[t.outcome] = self.by_outcome.get(t.outcome, 0) + 1
        if t.r_multiple > 0:
            self.wins += 1
        else:
            self.losses += 1

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def expectancy(self) -> float:
        """Average R per trade. The only number that matters."""
        return self.total_r / self.n if self.n else 0.0

    @property
    def max_drawdown_r(self) -> float:
        peak = equity = 0.0
        worst = 0.0
        for r in self.r_values:
            equity += r
            peak = max(peak, equity)
            worst = min(worst, equity - peak)
        return worst

    @property
    def profit_factor(self) -> Optional[float]:
        gains = sum(r for r in self.r_values if r > 0)
        pains = -sum(r for r in self.r_values if r < 0)
        return (gains / pains) if pains > 0 else None

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "win_rate": round(self.win_rate, 3),
            "expectancy_r": round(self.expectancy, 3),
            "total_r": round(self.total_r, 2),
            "max_drawdown_r": round(self.max_drawdown_r, 2),
            "profit_factor": (round(self.profit_factor, 2)
                              if self.profit_factor is not None else None),
            "by_outcome": self.by_outcome,
            "reliable": self.n >= 30,
        }


def split(bars: List[Bar], train_frac: float = 0.6) -> Tuple[List[Bar], List[Bar]]:
    """Chronological split. Never random — shuffling time series leaks the
    future into the training set."""
    cut = int(len(bars) * train_frac)
    return bars[:cut], bars[cut:]


def verdict(train: Stats, test: Stats) -> dict:
    """The honest read on a result.

    Three ways a backtest fools you, all checked here:
      - too few trades to mean anything
      - good in-sample, bad out-of-sample (the usual one)
      - a handful of outliers carrying the whole result
    """
    notes: List[str] = []

    if test.n < 30:
        notes.append(
            f"Only {test.n} out-of-sample trades. Under ~30 the win rate is "
            f"noise; this number cannot distinguish an edge from luck.")

    if train.n and test.n:
        drop = train.expectancy - test.expectancy
        if drop > 0.3:
            notes.append(
                f"Expectancy fell {drop:.2f}R from train to test. That gap is "
                f"the signature of rules fitted to the training data.")
        elif test.expectancy > train.expectancy + 0.3:
            notes.append(
                "Out-of-sample beat in-sample by a wide margin. Usually means "
                "the two periods had different conditions, not that the rules "
                "improved. Check the date ranges.")

    if test.r_values:
        top = sorted(test.r_values, reverse=True)[:3]
        if test.total_r > 0 and sum(top) > test.total_r * 0.8:
            notes.append(
                "Three trades account for over 80% of the out-of-sample result. "
                "Remove them and there is nothing here.")

    if test.expectancy <= 0 and test.n >= 30:
        notes.append(
            "Negative expectancy out-of-sample. On this data and these rules, "
            "the setup did not pay.")

    return {
        "train": train.to_dict(),
        "test": test.to_dict(),
        "notes": notes,
        "headline": (
            "Not enough out-of-sample trades to say anything." if test.n < 30
            else f"{test.expectancy:+.2f}R per trade out-of-sample "
                 f"over {test.n} trades."),
        "caveat": "Past behaviour on one instrument over one period. "
                  "Costs modelled, not measured. Not a forecast.",
    }
