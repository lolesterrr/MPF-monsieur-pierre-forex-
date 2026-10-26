"""Hand-built bars with known answers. Rulebook §0 no-lookahead is the key test."""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.bias import daily_bias, h1_profile, weekly_bias           # noqa: E402
from src.core.market_hours import is_open                               # noqa: E402
from src.core.sessions import build_asian_ranges, classify_break        # noqa: E402
from src.core.types import Bar, Direction, LevelStatus, Timeframe       # noqa: E402
from src.detectors.crt import find_crts                                 # noqa: E402
from src.detectors.levels import (                                      # noqa: E402
    find_fvgs, find_order_block_candidates, update_level_status,
)
from src.detectors.model1 import find_model1                            # noqa: E402
from src.detectors.swings import find_swings, mark_sweeps               # noqa: E402

UTC = ZoneInfo("UTC")
T0 = int(datetime(2026, 1, 5, 0, 0, tzinfo=UTC).timestamp())


def b(i, o, h, l, c, step=3600):
    return Bar(ts=T0 + i * step, open=o, high=h, low=l, close=c)


PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got}\n         want {want}")


print("\nswings")
# index:      0            1            2            3(high)      4            5            6
bars = [
    b(0, 1.0, 1.0010, 0.9990, 1.0000), b(1, 1.0, 1.0020, 1.0000, 1.0010),
    b(2, 1.0, 1.0030, 1.0010, 1.0020), b(3, 1.0, 1.0060, 1.0020, 1.0050),
    b(4, 1.0, 1.0040, 1.0010, 1.0020), b(5, 1.0, 1.0030, 1.0000, 1.0010),
    b(6, 1.0, 1.0020, 0.9995, 1.0000),
]
sw = find_swings(bars, n=2)
highs = [s for s in sw if s.is_high]
check("one swing high found", len(highs), 1)
check("swing high at index 3", highs[0].index, 3)
check("confirmed 2 bars later (no lookahead)", highs[0].confirmed_index, 5)

print("\nswing sweeps")
sweep_bars = bars + [b(7, 1.0, 1.0070, 1.0010, 1.0065)]
sw2 = mark_sweeps(sweep_bars, find_swings(sweep_bars, n=2))
h2 = [s for s in sw2 if s.is_high and s.index == 3][0]
check("swing high swept by later bar", h2.swept_index, 7)
check("and mitigated (closed through)", h2.mitigated_index, 7)

print("\nFVG")
fvg_bars = [
    b(0, 1.0, 1.0010, 0.9990, 1.0000),
    b(1, 1.0, 1.0050, 1.0005, 1.0045),
    b(2, 1.0, 1.0060, 1.0020, 1.0050),   # low 1.0020 > bar0 high 1.0010
]
fv = find_fvgs(fvg_bars, Timeframe.H1)
check("bullish FVG detected", len(fv), 1)
check("FVG direction", fv[0].direction, Direction.BULLISH)
check("FVG bottom = bar0 high", fv[0].bottom, 1.0010)
check("FVG top = bar2 low", fv[0].top, 1.0020)

print("\nFVG invalidation: wick through does NOT kill it")
wick = fvg_bars + [b(3, 1.0020, 1.0030, 1.0005, 1.0025)]   # wicks below, closes above
update_level_status(wick, fv)
check("wick-through = SWEPT not dead", fv[0].status, LevelStatus.SWEPT)

fv2 = find_fvgs(fvg_bars, Timeframe.H1)
closed = fvg_bars + [b(3, 1.0020, 1.0025, 1.0000, 1.0005)]  # closes below bottom
update_level_status(closed, fv2)
check("close-through = INVALIDATED", fv2[0].status, LevelStatus.INVALIDATED)

print("\norder block candidates")
ob_bars = [b(i, 1.0, 1.0010, 1.0000, 1.0005) for i in range(20)]  # ATR = 10 pips
ob_bars.append(b(20, 1.0005, 1.0015, 1.0000, 1.0012))   # up-close -> the OB
ob_bars.append(b(21, 1.0012, 1.0014, 0.9970, 0.9975))   # 44 pip down displacement
obs = find_order_block_candidates(ob_bars, Timeframe.H1)
check("one OB candidate", len(obs), 1)
check("bearish OB", obs[0].direction, Direction.BEARISH)
check("origin = the up-close bar", obs[0].created_index, 20)
check("zone = full candle range", (obs[0].bottom, obs[0].top), (1.0000, 1.0015))
check("you judge it, not the code", obs[0].meta["accepted"], None)

print("\nCRT")
crt_bars = [
    b(0, 1.0000, 1.0050, 1.0000, 1.0040),   # C1 range 1.0000-1.0050
    b(1, 1.0040, 1.0070, 1.0030, 1.0045),   # C2 wicks above, closes inside
    b(2, 1.0045, 1.0050, 1.0010, 1.0015),   # C3 the trade bar
]
crts = find_crts(crt_bars, Timeframe.H4)
check("bearish CRT found", len(crts), 1)
check("direction", crts[0].direction, Direction.BEARISH)
check("C3 index is ahead of C2 (no lookahead)", crts[0].c3_index, 2)
check("CRT 50%", round(crts[0].eq, 5), 1.0025)
check("opposing range = C1 low", crts[0].opposing, 1.0000)

check("CRT against bias is dropped",
      len(find_crts(crt_bars, Timeframe.H4, bias=Direction.BULLISH)), 0)

print("\nCRT: both-sides sweep is skipped, not guessed")
amb = [
    b(0, 1.0000, 1.0050, 1.0000, 1.0040),
    b(1, 1.0040, 1.0070, 0.9980, 1.0020),   # swept both ends
    b(2, 1.0020, 1.0030, 1.0000, 1.0010),
]
check("ambiguous CRT ignored", len(find_crts(amb, Timeframe.H4)), 0)

print("\nCRT: reported when C2 closes, not when C3 closes")
# The bug this guards: the loop ran to len(bars) - 1 and read bars[i + 1].ts,
# so a C3 bar had to exist before the pattern was reported. resample() drops
# the forming bar, so "exists" meant "already closed" — the freshest CRT you
# could ever see was one whose trade candle was over. On a daily, a full day
# late. Nothing about detection needs C3: C1 and C2 are the whole pattern.
live = crt_bars[:2]                      # C1 and C2 only — C3 has not opened
live_crts = find_crts(live, Timeframe.H4)
check("CRT found from C1 + C2 alone", len(live_crts), 1)
check("...flagged as still tradeable", live_crts[0].c3_pending, True)
check("...direction still right", live_crts[0].direction, Direction.BEARISH)
check("...C2's timestamp carried, so there is a candle to focus",
      live_crts[0].c2_ts, live[1].ts)
check("...C3 projected one bar ahead of C2",
      live_crts[0].c3_ts, live[1].ts + Timeframe.H4.seconds)

check("a CRT whose C3 exists is NOT pending", crts[0].c3_pending, False)
check("...and takes C3's real timestamp, not a projection",
      crts[0].c3_ts, crt_bars[2].ts)

# No lookahead, still: C3's OHLC must never change the verdict.
import copy                                                          # noqa: E402
altered = copy.deepcopy(crt_bars)
altered[2] = b(2, 9.0, 9.9, 8.0, 8.5)    # nonsense C3
check("C3's prices never affect detection",
      [(c.direction, c.range_high, c.range_low)
       for c in find_crts(altered, Timeframe.H4)],
      [(c.direction, c.range_high, c.range_low)
       for c in find_crts(crt_bars, Timeframe.H4)])

print("\nCRT: a projected C3 skips the weekend")
# Friday's daily candle plus 24h is Saturday, when nothing opens. A CRT whose
# C2 is Friday trades on Sunday's candle, and saying "C3 opens Saturday" is
# an alarm set for a shut market.
FRI = int(datetime(2026, 1, 8, 22, 0, tzinfo=UTC).timestamp())   # Thu 22:00Z
def d(i, o, h, l, c):
    return Bar(ts=FRI + i * 86400, open=o, high=h, low=l, close=c)
wk = [d(0, 1.0000, 1.0050, 1.0000, 1.0040),
      d(1, 1.0040, 1.0070, 1.0030, 1.0045)]      # C2 = Friday's candle
wkc = find_crts(wk, Timeframe.D1)
check("weekend CRT found", len(wkc), 1)
check("...C3 does not open on a shut market",
      is_open(wkc[0].c3_ts), True)
check("...it opens Sunday, not Saturday",
      datetime.fromtimestamp(wkc[0].c3_ts, UTC).strftime("%a"), "Sun")

print("\nModel #1")
# Bearish: PLP taken at bar 1 (an up-close bar). Bar 2 closes below its low.
m1_bars = [
    b(0, 1.0000, 1.0010, 0.9990, 1.0005),
    b(1, 1.0005, 1.0030, 1.0000, 1.0025),   # up-close, the last one up
    b(2, 1.0025, 1.0028, 0.9995, 0.9998),   # closes below bar1 low -> armed
]
m1 = find_model1(m1_bars, Direction.BEARISH, search_from=1, tf=Timeframe.H1)
check("setup found", m1 is not None, True)
check("origin candle", m1.candle_index, 1)
check("limit level at its low", m1.entry, 1.0000)
check("stop above its high", m1.stop, 1.0030)
check("risk in pips", round(m1.risk / 0.0001, 1), 30.0)
check("market entry is the trigger's close", m1.trigger_close, 0.9998)
check("nothing is armed until the trigger candle has CLOSED",
      m1.armed_ts, m1_bars[2].ts + 3600)

m1b = find_model1(m1_bars, Direction.BEARISH, search_from=1, tf=Timeframe.H1,
                  stop_buffer=0.0002)
check("stop buffer sits beyond the candle (author: 1-3 pips)",
      round(m1b.stop, 5), 1.0032)

# Candles before the PLP was taken belong to the move INTO it.
check("an origin candle before the sweep is not used",
      find_model1(m1_bars, Direction.BEARISH, search_from=2,
                  tf=Timeframe.H1), None)

# The author: "the last down-close candle in the move up — often the one that
# liquidates the PLP, but it doesn't have to be." No lower-low requirement.
bull = [
    b(0, 1.0020, 1.0025, 1.0000, 1.0005),   # sweep bar, down-close
    b(1, 1.0005, 1.0018, 1.0003, 1.0015),   # up
    b(2, 1.0015, 1.0016, 1.0008, 1.0010),   # down-close, higher low: still counts
    b(3, 1.0010, 1.0030, 1.0009, 1.0024),   # closes above bar2 high -> armed
]
mb = find_model1(bull, Direction.BULLISH, search_from=0, tf=Timeframe.H1)
check("bullish: the LAST down-close candle, even with a higher low",
      mb.candle_index, 2)
check("...armed by bar 3's close", mb.trigger_index, 3)
check("until_ts: a trigger that closes after C3 ends does not count",
      find_model1(bull, Direction.BULLISH, search_from=0, tf=Timeframe.H1,
                  until_ts=bull[3].ts), None)

print("\nbias")
pw = b(0, 1.0, 1.1000, 1.0800, 1.0900)
check("close below prev low = bearish",
      weekly_bias(pw, b(1, 1.09, 1.0950, 1.0700, 1.0750), 1).direction,
      Direction.BEARISH)
check("wick above prev high, closed inside = bearish",
      weekly_bias(pw, b(1, 1.09, 1.1100, 1.0850, 1.0950), 1).direction,
      Direction.BEARISH)
check("wick below prev low, closed inside = bullish",
      weekly_bias(pw, b(1, 1.09, 1.0990, 1.0700, 1.0850), 1).direction,
      Direction.BULLISH)
check("inside week = no bias",
      weekly_bias(pw, b(1, 1.09, 1.0950, 1.0850, 1.0900), 1).direction,
      Direction.NONE)

pd = b(0, 1.0, 1.1000, 1.0800, 1.0900)
check("outside day = skip, not a guess",
      daily_bias(pd, b(1, 1.09, 1.1050, 1.0750, 1.0900), 1).direction,
      Direction.NONE)
check("close above prev high = bullish",
      daily_bias(pd, b(1, 1.09, 1.1100, 1.0880, 1.1050), 1).direction,
      Direction.BULLISH)

print("\n1H profile gate")
check("HH + HL = bullish", h1_profile([1.10, 1.11], [1.09, 1.095]).direction,
      Direction.BULLISH)
check("LH + LL = bearish", h1_profile([1.11, 1.10], [1.095, 1.09]).direction,
      Direction.BEARISH)
check("mixed = skip the trade", h1_profile([1.10, 1.11], [1.095, 1.09]).direction,
      Direction.NONE)

print("\nAsian range (20:00-00:00 NY)")
ny = ZoneInfo("America/New_York")
start = datetime(2026, 3, 9, 20, 0, tzinfo=ny)      # Monday evening, post-DST
m5 = []
for k in range(48):                                  # 4 hours of 5m bars
    t = start + timedelta(minutes=5 * k)
    px = 1.0800 + (0.0005 if k == 20 else 0.0) - (0.0005 if k == 30 else 0.0)
    m5.append(Bar(ts=int(t.timestamp()), open=px, high=px + 0.0002,
                  low=px - 0.0002, close=px))
rngs = build_asian_ranges(m5)
check("one session bucketed", len(rngs), 1)
check("belongs to the NEXT NY day", list(rngs)[0], "2026-03-10")
r = rngs["2026-03-10"]
check("range high", round(r.high, 4), 1.0807)
check("range low", round(r.low, 4), 1.0793)

print("\ntrue vs false break")
after = [Bar(ts=r.end_ts + 300, open=1.0805, high=1.0815, low=1.0800, close=1.0804)]
check("wick beyond + close inside = Scenario A",
      classify_break(after, r, 0)["scenario"], "A")
after2 = [Bar(ts=r.end_ts + 300, open=1.0805, high=1.0820, low=1.0800, close=1.0818)]
check("5m close beyond = Scenario B",
      classify_break(after2, r, 0)["scenario"], "B")

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
