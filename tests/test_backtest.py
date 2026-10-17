"""Backtest engine tests. The pessimism rules are the ones that matter —
without them every range strategy backtests beautifully."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.engine import (                                    # noqa: E402
    Fills, Stats, Trade, simulate, split, verdict,
)
from src.backtest.replay import evaluate, find_setups                # noqa: E402
from src.core.types import Bar, Direction                            # noqa: E402
from src.data.feed import synthetic                                  # noqa: E402

T0 = int(datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")).timestamp())
PASS = FAIL = 0


def b(i, o, h, l, c):
    return Bar(ts=T0 + i * 300, open=o, high=h, low=l, close=c)


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got}\n         want {want}")


NOCOST = Fills(spread_pips=0, slippage_pips=0)


print("\ntrade simulation — long")
# entry 1.1000, stop 1.0980 (20 pips risk), t1 1.1020, t2 1.1040
def mk(dirn=Direction.BULLISH):
    return Trade(direction=dirn, entry_ts=T0, entry=1.1000, stop=1.0980,
                 t1=1.1020, t2=1.1040)

bars = [b(0, 1.1000, 1.1025, 1.0995, 1.1020),   # hits t1
        b(1, 1.1020, 1.1045, 1.1015, 1.1040)]   # hits t2
t = simulate(mk(), bars, 0, NOCOST)
check("reaches t2", t.outcome, "t2")
check("t1 was banked on the way", t.hit_t1, True)
# 0.6 * 20 pips + 0.4 * 40 pips = 12 + 16 = 28 pips on 20 risk = 1.4R
check("R is the 60/40 blend, not the full target", round(t.r_multiple, 2), 1.4)

print("\nstopped out")
t = simulate(mk(), [b(0, 1.1000, 1.1005, 1.0975, 1.0980)], 0, NOCOST)
check("outcome", t.outcome, "stopped")
check("loses exactly 1R", round(t.r_multiple, 2), -1.0)

print("\nt1 then stopped — the no-breakeven case from the doc")
t = simulate(mk(), [b(0, 1.1000, 1.1025, 1.0995, 1.1020),
                    b(1, 1.1020, 1.1022, 1.0975, 1.0980)], 0, NOCOST)
check("outcome is distinguished", t.outcome, "t1_then_stopped")
# 0.6 * +20 pips, 0.4 * -20 pips = +12 - 8 = +4 pips on 20 = 0.2R
check("partial still nets positive", round(t.r_multiple, 2), 0.2)

print("\nambiguous bar assumes the STOP first")
# one bar spanning stop AND t2 — OHLC can't tell us the order
t = simulate(mk(), [b(0, 1.1000, 1.1050, 1.0975, 1.1000)], 0, NOCOST)
check("pessimistic: counted as a stop", t.outcome, "stopped")
check("not credited with the target", t.r_multiple < 0, True)

print("\nshort mirrors long")
s = Trade(direction=Direction.BEARISH, entry_ts=T0, entry=1.1000, stop=1.1020,
          t1=1.0980, t2=1.0960)
t = simulate(s, [b(0, 1.1000, 1.1005, 1.0975, 1.0980),
                 b(1, 1.0980, 1.0985, 1.0955, 1.0960)], 0, NOCOST)
check("short reaches t2", t.outcome, "t2")
check("same 1.4R", round(t.r_multiple, 2), 1.4)

print("\ncosts are charged")
t = simulate(mk(), [b(0, 1.1000, 1.1005, 1.0975, 1.0980)], 0,
             Fills(spread_pips=1.0, slippage_pips=0.2))
check("a loss costs MORE than 1R with spread", t.r_multiple < -1.0, True)

print("\ntimeout marks to market, doesn't pretend it closed flat")
flat = [b(i, 1.1000, 1.1002, 1.0998, 1.1000) for i in range(10)]
t = simulate(mk(), flat, 0, NOCOST, max_bars=5)
check("outcome", t.outcome, "timeout")
check("bars held capped", t.bars_held, 5)

print("\nzero-risk trade rejected")
bad = Trade(direction=Direction.BULLISH, entry_ts=T0, entry=1.1, stop=1.1,
            t1=1.11, t2=1.12)
check("invalid, not a divide-by-zero", simulate(bad, flat, 0, NOCOST).outcome,
      "invalid")

print("\nstats")
st = Stats()
for r in (1.4, -1.0, 1.4, -1.0, 0.2):
    tt = mk(); tt.r_multiple = r; st.add(tt)
check("n", st.n, 5)
check("wins", st.wins, 3)
check("expectancy", round(st.expectancy, 3), 0.2)
check("max drawdown is negative or zero", st.max_drawdown_r <= 0, True)
check("under 30 trades flagged unreliable", st.to_dict()["reliable"], False)

print("\ntrain/test split is chronological")
bs = [b(i, 1.1, 1.1, 1.1, 1.1) for i in range(100)]
tr, te = split(bs, 0.6)
check("train size", len(tr), 60)
check("test size", len(te), 40)
check("test starts after train ends", te[0].ts > tr[-1].ts, True)

print("\nverdict catches the ways a backtest lies")
small = Stats()
for _ in range(5):
    tt = mk(); tt.r_multiple = 1.0; small.add(tt)
v = verdict(small, small)
check("flags too-few trades", any("noise" in n for n in v["notes"]), True)
check("headline refuses to claim an edge",
      "Not enough" in v["headline"], True)

big_train, big_test = Stats(), Stats()
for _ in range(40):
    a = mk(); a.r_multiple = 0.8; big_train.add(a)
    c = mk(); c.r_multiple = -0.1; big_test.add(c)
v = verdict(big_train, big_test)
check("flags train/test collapse as overfitting",
      any("fitted to the training data" in n for n in v["notes"]), True)
check("flags negative out-of-sample",
      any("did not pay" in n for n in v["notes"]), True)

outlier_tr, outlier_te = Stats(), Stats()
for _ in range(40):
    a = mk(); a.r_multiple = 0.05; outlier_tr.add(a);
for i in range(40):
    c = mk(); c.r_multiple = 10.0 if i < 3 else 0.01; outlier_te.add(c)
v = verdict(outlier_tr, outlier_te)
check("flags outlier-carried result",
      any("80%" in n for n in v["notes"]), True)

print("\nreplay over synthetic data (shape only — the data is noise)")
bars = synthetic(n=9000)
setups = find_setups(bars)
print(f"       {len(setups)} setups found")
check("replay runs without error", isinstance(setups, list), True)
check("every setup has a simulated outcome",
      all(s.trade.outcome != "open" for s in setups), True)
check("no setup has a target behind its entry",
      all((s.trade.t1 > s.trade.entry) == (s.direction is Direction.BULLISH)
          for s in setups), True)

print("\nevaluate() reports train and test separately")
ev = evaluate(bars, train_frac=0.6)
check("has train", "train" in ev, True)
check("has test", "test" in ev, True)
check("carries the caveat", "Not a forecast" in ev["caveat"], True)
check("per-condition breakdown warns about multiple comparisons",
      "multiple-comparisons" in ev["by_condition"]["_warning"], True)
check("costs are recorded in the output", ev["fills"]["spread_pips"], 1.0)

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
