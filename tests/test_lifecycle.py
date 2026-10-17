"""Bias invalidation and the CRT cap. Rulebook §11 — all defaults are [D]."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.lifecycle import BiasState, TargetProgress, invalidate_bias  # noqa: E402
from src.core.types import Bar, Direction, Level, LevelType, Timeframe     # noqa: E402

T0 = int(datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")).timestamp())
PASS = FAIL = 0


def b(i, o, h, l, c):
    return Bar(ts=T0 + i * 3600, open=o, high=h, low=l, close=c)


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}\n         got  {got}\n         want {want}")


REF_HIGH, REF_LOW = 1.1000, 1.0900

def bear():
    return BiasState(Direction.BEARISH, set_at_ts=T0, rule="W3 wick above")

def bull():
    return BiasState(Direction.BULLISH, set_at_ts=T0, rule="W4 wick below")


print("\nbias invalidation — the wick/close distinction")
wick = [b(1, 1.095, 1.1050, 1.0940, 1.0960)]      # wicks above, closes inside
s = invalidate_bias(bear(), wick, REF_HIGH, REF_LOW)
check("a wick above does NOT kill a bearish bias", s.live, True)
check("that wick is what the strategy expects", s.invalidated_ts, None)

close_above = [b(1, 1.095, 1.1060, 1.0940, 1.1030)]
s = invalidate_bias(bear(), close_above, REF_HIGH, REF_LOW)
check("a CLOSE above kills it", s.live, False)
check("and says why", s.invalidation_rule, "closed above the reference high")

print("\nbullish mirrors")
s = invalidate_bias(bull(), [b(1, 1.095, 1.0990, 1.0850, 1.0910)], REF_HIGH, REF_LOW)
check("wick below leaves a bullish bias alive", s.live, True)
s = invalidate_bias(bull(), [b(1, 1.095, 1.0990, 1.0840, 1.0870)], REF_HIGH, REF_LOW)
check("close below kills it", s.live, False)

print("\nbars at or before the bias was set are ignored")
old = [Bar(ts=T0 - 3600, open=1.1, high=1.12, low=1.09, close=1.115)]
check("no retroactive invalidation", invalidate_bias(bear(), old, REF_HIGH, REF_LOW).live, True)

print("\nno bias means nothing to invalidate")
none = BiasState(Direction.NONE, set_at_ts=T0, rule="W5 inside week")
check("stays not-live", invalidate_bias(none, close_above, REF_HIGH, REF_LOW).live, False)


print("\nCRT cap — consecutive losses")
tgt = Level(type=LevelType.SWING, direction=Direction.BEARISH,
            top=1.0800, bottom=1.0800, created_ts=T0, created_index=0,
            timeframe=Timeframe.D1)
p = TargetProgress(target=tgt)
check("open at the start", p.open_for_business, True)
p.record(won=False)
check("one loss, still open", p.open_for_business, True)
p.record(won=False)
check("two in a row stops it", p.open_for_business, False)
check("reason names the read, not the luck",
      "read is wrong" in p.stopped_reason, True)

print("\na win resets the streak")
# max_attempts raised so this isolates the streak rule from the attempt cap —
# at the default of 3, loss-win-loss stops on attempts, which is also correct.
p = TargetProgress(target=tgt, max_attempts=99)
p.record(won=False); p.record(won=True); p.record(won=False)
check("still open after loss-win-loss", p.open_for_business, True)
check("streak reset by the win", p.consecutive_losses, 1)

print("\nthe two caps are independent")
p = TargetProgress(target=tgt)   # defaults: 3 attempts, 2 losses
p.record(won=False); p.record(won=True); p.record(won=False)
check("at defaults, loss-win-loss stops on the attempt cap",
      "attempts at one target" in (p.stopped_reason or ""), True)

print("\nattempt cap")
p = TargetProgress(target=tgt, max_consecutive_losses=99)
p.record(True); p.record(True); p.record(True)
check("three attempts is the cap", p.open_for_business, False)
check("reason", "attempts at one target" in p.stopped_reason, True)

print("\ntarget reached stops new setups")
p = TargetProgress(target=tgt)
p.check_reached([b(1, 1.09, 1.0920, 1.0790, 1.0810)], Direction.BEARISH)
check("wick into the level counts", p.reached_ts is not None, True)
check("no longer accepting setups", p.open_for_business, False)
check("reason", p.stopped_reason, "daily target reached")

p = TargetProgress(target=tgt)
p.check_reached([b(1, 1.09, 1.0920, 1.0850, 1.0880)], Direction.BEARISH)
check("not reached when price stays above", p.open_for_business, True)

print("\nserialises for the dashboard")
d = TargetProgress(target=tgt).to_dict()
check("carries the gate", d["accepting_new_setups"], True)
check("carries the cap", d["max_attempts"], 3)

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
