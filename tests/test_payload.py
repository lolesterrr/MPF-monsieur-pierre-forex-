"""Payload shape tests — what the browser is actually handed.

The bug these exist for was silent. Every timeframe shipped its last 300 bars
while levels were detected over the whole history, so on 15m roughly half the
levels carried timestamps that had already fallen off the front of the window.
Clicking one did not error: it clamped to bar 0 and the focus bar read out
that bar's OHLC as the order block's origin candle. A confident wrong answer,
which on a chart you are using to decide whether to risk money is worse than
no answer at all.

Nothing in the detector tests could catch it. The levels were correct; the
window shipped alongside them was not.
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.types import Bar, Timeframe                            # noqa: E402
from src.pipeline import WINDOW_MAX, WINDOW_MIN, _window, run        # noqa: E402

T0 = int(datetime(2026, 1, 5, 22, tzinfo=ZoneInfo("UTC")).timestamp())
PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got}\n         want {want}")


def bars(n, step=300, start=T0):
    """A gently trending series, enough structure to produce levels."""
    out, px = [], 1.1000
    for i in range(n):
        px += 0.0004 * (1 if (i // 17) % 2 == 0 else -1)
        o = px
        c = px + (0.0006 if (i // 5) % 2 == 0 else -0.0006)
        out.append(Bar(ts=start + i * step, open=o, close=c,
                       high=max(o, c) + 0.0003, low=min(o, c) - 0.0003))
    return out


print("\n_window: reaches the oldest level it has to show")

bs = bars(1000)
check("no level asked for — floor applies", len(_window(bs, None)), WINDOW_MIN)
check("level inside the floor — floor still applies",
      len(_window(bs, bs[-50].ts)), WINDOW_MIN)

# A level 600 bars back must be inside the window, with context before it.
w = _window(bs, bs[-600].ts)
check("level 600 back is inside the window", w[0].ts <= bs[-600].ts, True)
check("...with context bars before it", len(w) > 600, True)

check("ceiling holds", len(_window(bars(3000), bars(3000)[0].ts)), WINDOW_MAX)
check("empty series stays empty", _window([], T0), [])

# Older than anything held: ship everything rather than silently clamping.
check("level older than every bar ships the lot",
      len(_window(bs, T0 - 10_000_000)), 1000)


print("\nevery level the payload emits is inside its own candle window")

payload = run(bars(2400, step=300), symbol="EURUSD")
candles = payload["candles"]
check("payload has candles", bool(candles), True)
check("payload has levels", len(payload["levels"]) > 0, True)

outside = []
for lv in payload["levels"]:
    series = candles.get(lv["tf"])
    if not series:
        outside.append((lv["id"], "no series for this timeframe"))
        continue
    if not (series[0]["t"] <= lv["ts"] <= series[-1]["t"]):
        outside.append((lv["id"], f"{series[0]['t']}..{series[-1]['t']}"))

check("no level points outside the bars it is drawn on", outside, [])


print("\nclickable rows carry what the chart needs to find them")

for row in payload["confluences"]:
    series = candles.get(row["tf"])
    if not series:
        check(f"confluence {row['id']}: has a series", False, True)
        continue
    inside = series[0]["t"] <= row["ts"] <= series[-1]["t"]
    check(f"confluence {row['id']} ({row['kind']}) lands in its window",
          inside, True)

aligned = [c for c in payload["confluences"] if c["kind"] == "aligned"]
check("aligned rows carry the level id, so a dropped block can be matched",
      all("level_id" in c for c in aligned), True)

targets = [c for c in payload["confluences"] if c["kind"] == "target"]
check("target rows carry edges, so a region does not draw as an arrow",
      all("top" in c and "bottom" in c for c in targets), True)

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
