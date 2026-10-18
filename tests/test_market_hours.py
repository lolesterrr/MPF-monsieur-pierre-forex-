"""Forex session hours and cleaning. The weekend-gap FVG is the bug this prevents."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.market_hours import (                                   # noqa: E402
    clean, is_open, market_status, next_open, same_session, session_id,
)
from src.core.types import Bar, Timeframe                             # noqa: E402
from src.detectors.levels import find_fvgs                            # noqa: E402

NY = ZoneInfo("America/New_York")
PASS = FAIL = 0


def ts(y, mo, d, h, mi=0, tz=NY):
    return int(datetime(y, mo, d, h, mi, tzinfo=tz).timestamp())


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}\n         got {got} want {want}")


print("\nsession hours (New York)")
check("Wednesday noon is open", is_open(ts(2026,10,14,12)), True)
check("Friday 16:59 still open", is_open(ts(2026,10,16,16,59)), True)
check("Friday 17:00 shut", is_open(ts(2026,10,16,17,0)), False)
check("Friday 23:00 shut", is_open(ts(2026,10,16,23,0)), False)
check("Saturday is shut all day", is_open(ts(2026,10,17,12)), False)
check("Saturday 23:00 shut", is_open(ts(2026,10,17,23)), False)
check("Sunday 16:59 still shut", is_open(ts(2026,10,18,16,59)), False)
check("Sunday 17:00 opens", is_open(ts(2026,10,18,17,0)), True)
check("Monday 03:00 open", is_open(ts(2026,10,19,3)), True)

print("\nsessions are separated by the weekend")
fri = Bar(ts=ts(2026,10,16,16,55), open=1.085,high=1.0854,low=1.0846,close=1.085)
mon = Bar(ts=ts(2026,10,19,3,0), open=1.092,high=1.0924,low=1.0916,close=1.092)
wed = Bar(ts=ts(2026,10,14,12,0), open=1.080,high=1.0804,low=1.0796,close=1.080)
check("Friday and Monday are different sessions", same_session(fri, mon), False)
check("Wednesday and Friday are the same session", same_session(wed, fri), True)

print("\ncleaning removes what shouldn't exist")
pad = [Bar(ts=ts(2026,10,17,h), open=1.085,high=1.085,low=1.085,close=1.085)
       for h in range(0, 24, 4)]
future = [Bar(ts=ts(2030,1,2,12), open=1.1,high=1.11,low=1.09,close=1.1)]
real = [wed, fri, mon]
out, rep = clean(real + pad + future, "EURUSD", now=ts(2026,10,20,12))

check("real bars survive", len(out), 3)
check("weekend padding removed", rep.removed_closed, 6)
check("future bars removed", rep.removed_future, 1)
check("one session break found", len(rep.session_breaks), 1)

print("\nflat padding inside market hours is still removed")
flat = Bar(ts=ts(2026,10,14,13), open=1.08,high=1.08,low=1.08,close=1.08)
out2, rep2 = clean([wed, flat, fri], "EURUSD", now=ts(2026,10,20,12))
check("zero-range zero-volume bar dropped", rep2.removed_flat, 1)

print("\nfutures keep their own calendar — not cleaned")
out3, rep3 = clean(pad, "NQ", now=ts(2026,10,20,12))
check("NQ untouched", len(out3), len(pad))
check("and says why", rep3.skipped, True)

print("\nTHE BUG THIS PREVENTS: weekend gap becoming an FVG")
# Friday close 1.0850, Monday open 1.0920 -> a 70 pip 'gap' that is just the weekend
span = [
    Bar(ts=ts(2026,10,16,16,50), open=1.0850,high=1.0854,low=1.0846,close=1.0850),
    Bar(ts=ts(2026,10,16,16,55), open=1.0850,high=1.0854,low=1.0846,close=1.0850),
    Bar(ts=ts(2026,10,18,17,0),  open=1.0920,high=1.0924,low=1.0916,close=1.0920),
    Bar(ts=ts(2026,10,18,17,5),  open=1.0920,high=1.0924,low=1.0916,close=1.0920),
]
naive = find_fvgs(span, Timeframe.M5, session_aware=False)
guarded = find_fvgs(span, Timeframe.M5)
print(f"       without the guard: {len(naive)} FVG(s)")
print(f"       with the guard:    {len(guarded)} FVG(s)")
check("the weekend gap is NOT an FVG", len(guarded), 0)
check("and the unguarded version does find one (so the test is real)", len(naive) > 0, True)

print("\nmarket status is information, not an error")
closed = market_status(now=ts(2026,10,17,12))
check("Saturday reports closed", closed["open"], False)
check("and explains rather than alarms", "not a stale feed" in closed["detail"], True)
check("Wednesday reports open", market_status(now=ts(2026,10,14,12))["open"], True)

print("\nnext_open: projecting a bar forward has to skip the weekend")
# Used to say when a CRT's C3 opens. Adding one bar length to Friday's daily
# candle lands on Saturday, and an alarm set for a shut market is worse than
# no alarm.
check("mid-week is already open, so unchanged",
      next_open(ts(2026, 10, 14, 10)), ts(2026, 10, 14, 10))
check("Friday 17:00 (the close) rolls to Sunday 17:00",
      next_open(ts(2026, 10, 16, 17)), ts(2026, 10, 18, 17))
check("mid-Saturday rolls to Sunday 17:00",
      next_open(ts(2026, 10, 17, 9)), ts(2026, 10, 18, 17))
check("Sunday before the open rolls to the open",
      next_open(ts(2026, 10, 18, 9)), ts(2026, 10, 18, 17))
check("Sunday after the open is left alone",
      next_open(ts(2026, 10, 18, 19)), ts(2026, 10, 18, 19))
check("whatever it returns is always a time the market is open",
      all(is_open(next_open(ts(2026, 10, 12 + d, h)))
          for d in range(7) for h in (0, 6, 12, 17, 18, 23)), True)

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
