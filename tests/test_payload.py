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

print("\ncache headers: the page and the chart layer must never disagree")

# `max-age=3600` on everything-but-HTML shipped a browser the new index.html
# and an hour-old chart.js. Both files were individually correct; together
# they threw on the first call to a function the old one had not exported
# yet, and the dashboard came up blank. Anything the app's own code lives in
# has to revalidate.
from src.wsgi import _cache_control, app                             # noqa: E402

check("index.html revalidates", _cache_control("/index.html", "text/html"),
      "no-cache")
check("chart.js revalidates — the one that broke",
      _cache_control("/chart.js", "text/javascript"), "no-cache")
check("so does anything else we write",
      _cache_control("/app.css", "text/css"), "no-cache")
check("pinned vendor code may be held",
      "immutable" in _cache_control("/vendor/lightweight-charts.js",
                                    "text/javascript"), True)
check("so may Pierre's sprite sheet",
      "immutable" in _cache_control("/assets/sheet-pierre.png", "image/png"),
      True)


def fetch(path, headers=None):
    """One request through the real WSGI callable."""
    env = {"PATH_INFO": path, "QUERY_STRING": "", "REQUEST_METHOD": "GET",
           "REMOTE_ADDR": "127.0.0.1"}
    env.update(headers or {})
    out = {}

    def start(status, hdrs):
        out["status"] = int(status.split()[0])
        out["headers"] = {k.lower(): v for k, v in hdrs}

    body = b"".join(app(env, start))
    return out["status"], out["headers"], body


status, headers, body = fetch("/chart.js")
check("chart.js serves", status, 200)
check("...with an ETag", "etag" in headers, True)
check("...and the page's markup can rely on what it exports",
      b"setDropped" in body, True)

etag = headers["etag"]
s2, _, b2 = fetch("/chart.js", {"HTTP_IF_NONE_MATCH": etag})
check("a matching ETag revalidates cheaply", (s2, b2), (304, b""))

s3, _, _ = fetch("/chart.js", {"HTTP_IF_NONE_MATCH": '"stale0000000000"'})
check("a stale ETag re-sends the file", s3, 200)

# The actual invariant: whatever the page calls on Chart, Chart must export.
import re                                                            # noqa: E402

page = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text()
chart = (Path(__file__).resolve().parents[1] / "web" / "chart.js").read_text()
called = set(re.findall(r"\bChart\.(\w+)\s*\(", page))
exported = set(re.findall(r"\bChart\.(\w+)\s*\(", chart))
tail = chart[chart.rindex("return {"):]
for name in re.findall(r"[\w]+", tail):
    exported.add(name)
missing = sorted(called - exported)
check("every Chart.* the page calls is exported by chart.js", missing, [])

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
