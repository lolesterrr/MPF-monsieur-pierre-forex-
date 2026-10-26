"""The C3 entry chain, as the strategy's author described it (Rulebook §7).

Each block pins one answer he gave, so a later "simplification" that drifts
back to the old behaviour fails here rather than quietly in a backtest.
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.engine import Fills                                # noqa: E402
from src.backtest.replay import _limit_fill, find_setups             # noqa: E402
from src.core.confluence import broke_structure, mark_plp            # noqa: E402
from src.core.entry import (                                         # noqa: E402
    arm_time, c3_tradeable, entry_timeframe, rr,
)
from src.core.types import Bar, Direction, Timeframe                 # noqa: E402
from src.data.feed import resample, synthetic                        # noqa: E402
from src.detectors.swings import find_swings, mark_sweeps            # noqa: E402

NY = ZoneInfo("America/New_York")
PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got}\n         want {want}")


def ny(ts):
    return datetime.fromtimestamp(ts, NY).strftime("%a %H:%M")


def flat(start, n, step=300):
    t = int(start.timestamp())
    return [Bar(ts=t + i * step, open=1.1, high=1.1001, low=1.0999, close=1.1)
            for i in range(n)]


print("\ncandles sit on the New York clock, summer and winter")
for label, start in (("summer (EDT)", datetime(2026, 10, 18, 17, tzinfo=NY)),
                     ("winter (EST)", datetime(2026, 11, 8, 17, tzinfo=NY))):
    week = flat(start, 12 * 24 * 5)
    h4 = resample(week, Timeframe.H4)
    check(f"{label}: 4H opens 17/21/01/05/09/13 NY",
          [ny(b.ts) for b in h4[:6]],
          ["Sun 17:00", "Sun 21:00", "Mon 01:00", "Mon 05:00",
           "Mon 09:00", "Mon 13:00"])
    d1 = resample(week, Timeframe.D1)
    check(f"{label}: the day opens 17:00 NY", {ny(b.ts)[4:] for b in d1},
          {"17:00"})
    check(f"{label}: one full week, opening Sunday 17:00",
          [ny(b.ts) for b in resample(week, Timeframe.W1)], ["Sun 17:00"])


print("\nwhich C3s get traded")
h = lambda hr: int(datetime(2026, 10, 20, hr, tzinfo=NY).timestamp())
check("4H C3 at 01/05/09/13 NY is traded",
      [c3_tradeable(Timeframe.H4, h(x)) for x in (1, 5, 9, 13)], [True] * 4)
check("4H C3 at 17/21 NY is not",
      [c3_tradeable(Timeframe.H4, h(x)) for x in (17, 21)], [False, False])
check("a daily C3 is marked at 00:00 NY, not at its 17:00 open",
      ny(arm_time(Timeframe.D1, h(17) - 86400)), "Tue 00:00")


print("\nentry timeframe")
check("daily CRT -> 15M", entry_timeframe(Timeframe.D1, False), Timeframe.M15)
check("daily CRT with a 1H PD array below the PLP -> 5M",
      entry_timeframe(Timeframe.D1, True), Timeframe.M5)
check("4H CRT -> 5M", entry_timeframe(Timeframe.H4, False), Timeframe.M5)


print("\nPLP is the low that broke a high")
T = int(datetime(2026, 10, 19, 1, tzinfo=NY).timestamp())


def hb(i, h_, l_, c_=None):
    c_ = c_ if c_ is not None else (h_ + l_) / 2
    return Bar(ts=T + i * 3600, open=(h_ + l_) / 2, high=h_, low=l_, close=c_)


# high at 2 (1.1050), pull back to a low at 5 (1.1000), rally closes above
# 1.1050 at bar 8 -> the low at 5 broke a high.
mss = [hb(0, 1.1020, 1.1010), hb(1, 1.1030, 1.1015), hb(2, 1.1050, 1.1030),
       hb(3, 1.1040, 1.1020), hb(4, 1.1030, 1.1010), hb(5, 1.1015, 1.1000),
       hb(6, 1.1025, 1.1008), hb(7, 1.1040, 1.1012), hb(8, 1.1060, 1.1030, 1.1058),
       hb(9, 1.1062, 1.1040), hb(10, 1.1061, 1.1045)]
sw = mark_sweeps(mss, find_swings(mss))
low5 = next(s for s in sw if not s.is_high and s.index == 5)
check("the low at bar 5 broke the high before it", broke_structure(mss, sw, low5, 10), True)
check("...but not before the close that did it (no lookahead)",
      broke_structure(mss, sw, low5, 7), False)
plp = mark_plp(mss, sw, Direction.BULLISH, as_of_index=10, tf=Timeframe.H1)
check("mark_plp picks it", plp and plp.price, 1.1000)

# Same shape, but the rally stalls under the high: an ordinary swing low.
weak = mss[:8] + [hb(8, 1.1045, 1.1030), hb(9, 1.1046, 1.1035),
                  hb(10, 1.1044, 1.1036)]
sw2 = mark_sweeps(weak, find_swings(weak))
check("a low whose rally never broke the high is not a PLP",
      mark_plp(weak, sw2, Direction.BULLISH, as_of_index=10, tf=Timeframe.H1),
      None)


# The author: "even a wick works". A wick through the high, closing back
# under it, still makes the low a PLP.
wick = mss[:8] + [hb(8, 1.1052, 1.1030, 1.1040), hb(9, 1.1046, 1.1035),
                  hb(10, 1.1044, 1.1036)]
sw3 = mark_sweeps(wick, find_swings(wick))
check("a wick through the high is enough",
      (mark_plp(wick, sw3, Direction.BULLISH, as_of_index=10,
                tf=Timeframe.H1) or type("x", (), {"price": None})).price,
      1.1000)

# Several qualifying lows: the most recent (L2 in the author's sketch) wins.
two = mss + [hb(11, 1.1055, 1.1042), hb(12, 1.1050, 1.1038),
             hb(13, 1.1058, 1.1043), hb(14, 1.1072, 1.1050),
             hb(15, 1.1075, 1.1060), hb(16, 1.1074, 1.1062)]
sw4 = mark_sweeps(two, find_swings(two))
p4 = mark_plp(two, sw4, Direction.BULLISH, as_of_index=16, tf=Timeframe.H1)
check("when several lows took out highs, the most recent is the PLP",
      p4 and p4.price, 1.1038)


print("\nRR rule: 2R to T1, at the close or at the limit")
check("rr long", rr(1.1020, 1.1010, 1.1040, Direction.BULLISH), 2.0)
check("rr short", round(rr(1.1000, 1.1010, 1.0970, Direction.BEARISH), 6), 3.0)

m5 = [Bar(ts=T + i * 300, open=1.1030, high=1.1035, low=1.1025, close=1.1030)
      for i in range(6)]
m5[3] = Bar(ts=m5[3].ts, open=1.1030, high=1.1031, low=1.1019, close=1.1022)
check("limit fills on the first bar that trades back to it",
      _limit_fill(m5, 0, 1.1020, 1.1080, Direction.BULLISH, T + 10_000), 3)
check("limit is cancelled once C3 has closed",
      _limit_fill(m5, 0, 1.1020, 1.1080, Direction.BULLISH, m5[2].ts), None)
m5[1] = Bar(ts=m5[1].ts, open=1.1030, high=1.1081, low=1.1028, close=1.1070)
check("limit is cancelled if T1 prints first",
      _limit_fill(m5, 0, 1.1020, 1.1080, Direction.BULLISH, T + 10_000), None)


print("\nno trade starts before its trigger candle has closed")
bars = synthetic(n=30000)
setups = find_setups(bars, fills=Fills(1.0, 0.2, "EURUSD"))
check("the synthetic run produces trades to check", len(setups) > 0, True)
check("no trade's first bar is before its trigger candle closed",
      [s.trade.entry_ts for s in setups if s.trade.entry_ts < s.armed_ts], [])
check("every trade clears 2R to T1 at its entry",
      all(rr(s.trade.entry, s.trade.stop, s.trade.t1, s.direction) >= 2
          for s in setups), True)

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
