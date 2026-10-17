"""Confluence engine tests. Rulebook §6, §8, §10."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.confluence import (                                    # noqa: E402
    MAX_SCORE, LevelPool, build_report, check_smt, mark_plp,
)
from src.core.types import (                                         # noqa: E402
    Bar, Direction, Level, LevelStatus, LevelType, Timeframe,
)
from src.detectors.swings import find_swings, mark_sweeps            # noqa: E402

T0 = int(datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")).timestamp())
PASS = FAIL = 0


def b(i, o, h, l, c):
    return Bar(ts=T0 + i * 3600, open=o, high=h, low=l, close=c)


def lv(t, d, top, bottom, tf=Timeframe.H4, idx=0):
    return Level(type=t, direction=d, top=top, bottom=bottom,
                 created_ts=T0, created_index=idx, timeframe=tf)


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got}\n         want {want}")


print("\nPLP selection")
# A swing high at index 3, then price drifts down and later reaches up for it.
bars = [
    b(0, 1.0, 1.0010, 0.9990, 1.0000), b(1, 1.0, 1.0020, 1.0000, 1.0010),
    b(2, 1.0, 1.0030, 1.0010, 1.0020), b(3, 1.0, 1.0060, 1.0020, 1.0050),
    b(4, 1.0, 1.0040, 1.0010, 1.0020), b(5, 1.0, 1.0030, 1.0000, 1.0010),
    b(6, 1.0, 1.0025, 0.9995, 1.0000), b(7, 1.0, 1.0070, 1.0000, 1.0020),
]
sw = mark_sweeps(bars, find_swings(bars, n=2))

plp = mark_plp(bars, sw, Direction.BEARISH, as_of_index=6, tf=Timeframe.H1)
check("bearish bias picks a swing HIGH", plp is not None and plp.price, 1.0060)
check("and sees it taken later", plp.taken_index, 7)

check("bullish bias wants a swing low, none unswept here",
      mark_plp(bars, sw, Direction.BULLISH, as_of_index=6, tf=Timeframe.H1) is None,
      True)
check("no bias -> no PLP",
      mark_plp(bars, sw, Direction.NONE, as_of_index=6, tf=Timeframe.H1), None)

print("\nPLP respects confirmation lag (no lookahead)")
# At index 4 the swing at 3 is not yet confirmed (confirms at 5).
check("unconfirmed swing is not offered",
      mark_plp(bars, sw, Direction.BEARISH, as_of_index=4, tf=Timeframe.H1), None)

print("\nlevel pool")
pool = LevelPool().add([
    lv(LevelType.FVG, Direction.BEARISH, 1.0900, 1.0880),
    lv(LevelType.SWING, Direction.BULLISH, 1.0800, 1.0800),
    lv(LevelType.ORDER_BLOCK, Direction.BULLISH, 1.0820, 1.0810),
])
dead = lv(LevelType.FVG, Direction.BULLISH, 1.0700, 1.0690)
dead.status = LevelStatus.INVALIDATED
pool.add([dead])

check("invalidated levels drop out", len(pool.valid()), 3)
check("filter by direction", len(pool.in_direction(Direction.BULLISH)), 2)

print("\nalignment (the whole point of the tool)")
near = pool.near(1.0881, tolerance_pips=5.0)
check("finds the level under price", len(near), 1)
check("and names it", near[0].type, LevelType.FVG)
check("5 pips means 5 pips", len(pool.near(1.0940, tolerance_pips=5.0)), 0)

print("\ntarget selection")
# Price in premium at 1.0900, eq 1.0850 -> must target something in discount.
t = pool.pick_target(Direction.BEARISH, price=1.0900, eq=1.0850)
check("target found below eq", t is not None, True)
check("swing beats FVG and OB when several qualify", t.type, LevelType.SWING)
check("nothing above eq is offered when in premium", t.top < 1.0850, True)

check("no bias -> no target",
      pool.pick_target(Direction.NONE, 1.09, 1.0850), None)

print("\nSMT divergence")
# EURUSD makes a lower low; GBPUSD does not.
eur = [b(0, 1.0, 1.010, 1.000, 1.005), b(1, 1.0, 1.008, 0.990, 0.995),
       b(2, 1.0, 1.006, 0.995, 1.000), b(3, 1.0, 1.004, 0.985, 0.990),
       b(4, 1.0, 1.002, 0.995, 1.000), b(5, 1.0, 1.006, 0.998, 1.004)]
gbp = [b(0, 1.3, 1.310, 1.300, 1.305), b(1, 1.3, 1.308, 1.290, 1.295),
       b(2, 1.3, 1.306, 1.295, 1.300), b(3, 1.3, 1.304, 1.292, 1.298),
       b(4, 1.3, 1.302, 1.295, 1.300), b(5, 1.3, 1.306, 1.298, 1.304)]
es = mark_sweeps(eur, find_swings(eur, n=1))
gs = mark_sweeps(gbp, find_swings(gbp, n=1))

smt = check_smt(eur, es, gbp, gs, Direction.BULLISH, as_of_index=5)
check("divergence detected", smt is not None and smt["divergence"], True)

print("\nSMT: no divergence when both follow")
gbp2 = [b(0, 1.3, 1.310, 1.300, 1.305), b(1, 1.3, 1.308, 1.290, 1.295),
        b(2, 1.3, 1.306, 1.295, 1.300), b(3, 1.3, 1.304, 1.285, 1.290),
        b(4, 1.3, 1.302, 1.295, 1.300), b(5, 1.3, 1.306, 1.298, 1.304)]
gs2 = mark_sweeps(gbp2, find_swings(gbp2, n=1))
check("both made lower lows -> no SMT",
      check_smt(eur, es, gbp2, gs2, Direction.BULLISH, as_of_index=5), None)

print("\nscoring")
empty = build_report(Direction.BEARISH)
check("nothing met scores zero", empty.score, 0)
check("max score is the sum of weights", empty.max_score, MAX_SCORE)
check("everything lands in missing", len(empty.missing), 10)

full = build_report(
    Direction.BEARISH, weekly_ok=True, daily_ok=True, h1_ok=True,
    correct_side=True, target=lv(LevelType.SWING, Direction.BEARISH, 1.08, 1.08),
    crt=type("C", (), {"timeframe": Timeframe.H4, "direction": Direction.BEARISH,
                       "eq": 1.0850})(),
    plp=type("P", (), {"price": 1.0900, "is_taken": True})(),
    plp_alignment=[lv(LevelType.FVG, Direction.BEARISH, 1.0902, 1.0898)],
    smt={"note": "EURUSD extended, comparison did not follow"},
    model1=type("M", (), {"entry": 1.0890, "stop": 1.0910,
                          "risk": 0.0020})(),
)
check("everything met hits max", full.score, MAX_SCORE)
check("nothing missing", full.missing, [])
check("report carries the caveat",
      "not a signal" in full.to_dict()["disclaimer"], True)

print("\npartial score reflects what is actually there")
partial = build_report(Direction.BEARISH, weekly_ok=True, daily_ok=True)
check("weekly + daily only = 4", partial.score, 4)
check("and the rest is listed as missing", len(partial.missing), 8)

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
