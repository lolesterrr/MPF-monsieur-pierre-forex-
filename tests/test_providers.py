"""History fetching. The part that decides whether a backtest can run at all.

The bug these exist for produced an HTTP 504 and nothing else. get_history
chose its pager with `max(pagers, key=max_per_request)` — "go as deep as you
can" — and Dukascopy advertises 100,000 bars against everyone else's 5,000,
so it always won. Dukascopy also issues one HTTP request per HOUR of history,
so a six-page pull was 50,000 requests from a function with 60 seconds to
live.

Every provider was individually fine. The chooser asked the wrong question:
how big is your page, rather than can you answer in time.

The live HTTP call cannot be exercised here (no market-data host is reachable
from the sandbox, and a test that needs the internet is not a test anyway), so
the transport is stubbed and everything around it is real.
"""

import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.types import Bar                                       # noqa: E402
from src.data import providers as P                                  # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got}\n         want {want}")


T0 = 1_700_000_000


class Stub(P.Provider):
    """A provider whose pages are real but whose network is not."""
    name = "stub"
    symbols = {"EURUSD": "EUR/USD"}
    granularity = {"5m": "5min"}
    supports_paging = True
    max_per_request = 100
    live_capable = True

    def __init__(self, total=1000, honours_cursor=True):
        self.calls: List[Optional[int]] = []
        self.total = total
        self.honours_cursor = honours_cursor

    def fetch(self, symbol, tf, count=100, before_ts=None, deadline=None):
        self.calls.append(before_ts)
        end = before_ts if (before_ts and self.honours_cursor) else T0 + self.total * 300
        return [Bar(ts=t, open=1.0, high=1.1, low=0.9, close=1.0)
                for t in range(end - count * 300, end, 300)
                if t >= T0]


class SlowStub(Stub):
    name = "slowstub"
    live_capable = False
    max_per_request = 100000


print("\npick_pager asks 'can you answer in time', not 'how big is your page'")

orig_providers, orig_by_name = P.PROVIDERS, P.BY_NAME
fast, slow = Stub(), SlowStub()
P.PROVIDERS = [fast, slow]
P.BY_NAME = {p.name: p for p in P.PROVIDERS}

check("interactive skips the slow deep pager",
      P.pick_pager("EURUSD", "5m", interactive=True).name, "stub")
check("offline takes the deepest available",
      P.pick_pager("EURUSD", "5m", interactive=False).name, "slowstub")
check("naming a provider overrides the choice",
      P.pick_pager("EURUSD", "5m", provider="slowstub", interactive=True).name,
      "slowstub")

# The exact shape of the 504: only a slow pager, called interactively.
P.PROVIDERS = [slow]
P.BY_NAME = {p.name: p for p in P.PROVIDERS}
check("no fast pager -> None, not the slow one",
      P.pick_pager("EURUSD", "5m", interactive=True), None)

try:
    P.get_history("EURUSD", "5m", pages=6, interactive=True)
    check("...and get_history refuses rather than hanging", "no error", "ProviderError")
except P.ProviderError as e:
    msg = str(e)
    check("...and get_history refuses rather than hanging", True, True)
    check("...naming the provider that could have done it",
          "slowstub" in msg, True)
    check("...and pointing somewhere useful", "docs/DATA.md" in msg, True)


print("\nget_history pages backwards and stops sensibly")

paging = Stub(total=1000)
P.PROVIDERS = [paging]
P.BY_NAME = {p.name: p for p in P.PROVIDERS}

bars = P.get_history("EURUSD", "5m", pages=5)
check("five pages of 100 bars", len(bars), 500)
check("...in chronological order", bars == sorted(bars, key=lambda b: b.ts), True)
check("...with no duplicate timestamps",
      len({b.ts for b in bars}), len(bars))
check("first call has no cursor", paging.calls[0], None)
check("later calls carry one", all(c is not None for c in paging.calls[1:]), True)
check("the cursor walks backwards",
      paging.calls[1:] == sorted(paging.calls[1:], reverse=True), True)

# A provider that ignores end_date returns the same page forever. Degrade to
# one page rather than spinning.
stuck = Stub(total=1000, honours_cursor=False)
P.PROVIDERS = [stuck]
P.BY_NAME = {p.name: p for p in P.PROVIDERS}
stuck_bars = P.get_history("EURUSD", "5m", pages=6)
check("a provider ignoring the cursor degrades to one page",
      len(stuck_bars), 100)
check("...and stops asking", len(stuck.calls), 2)

P.PROVIDERS, P.BY_NAME = orig_providers, orig_by_name


print("\nthe real chain: EURUSD 5m history")

chain = [p.name for p in P.PROVIDERS if p.supports("EURUSD", "5m")]
check("twelvedata is in the chain", "twelvedata" in chain, True)
td = P.BY_NAME["twelvedata"]
check("twelvedata can page now", td.supports_paging, True)
check("...and is fast enough to be picked interactively", td.live_capable, True)
check("dukascopy is still the deep offline option",
      P.BY_NAME["dukascopy"].supports_paging, True)
check("...but is not live_capable, so never picked interactively",
      P.BY_NAME["dukascopy"].live_capable, False)

# A provider with no key cannot answer, so it must not be chosen. Found by
# running the real route end to end: OANDA ties with Twelve Data on page size
# and sorts first, so the backtest died on "OANDA_TOKEN is not set" -- a
# missing key surfacing as a data failure. get_bars always skipped
# unconfigured providers; this path did not.
import os                                                            # noqa: E402
saved = {k: os.environ.get(k) for k in
         ("TWELVEDATA_KEY", "OANDA_TOKEN", "ALPHAVANTAGE_KEY")}
for k in saved:
    os.environ.pop(k, None)
check("with no keys at all, nothing fast can page",
      P.pick_pager("EURUSD", "5m", interactive=True), None)

os.environ["OANDA_TOKEN"] = "x"
check("a keyed provider becomes eligible once configured",
      P.pick_pager("EURUSD", "5m", interactive=True).name, "oanda")

os.environ["TWELVEDATA_KEY"] = "x"
picked = P.pick_pager("EURUSD", "5m", interactive=True)
check("interactive choice is never dukascopy", picked.name != "dukascopy", True)
check("...and is a provider that can actually answer",
      P.is_configured(picked) and picked.live_capable, True)

for k, v in saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

# Six pages of 5m from a 5000-bar pager is a real backtest window.
span_days = 6 * td.max_per_request * 300 / 86400
check("six pages covers enough history to backtest", span_days > 90, True)
print(f"       six pages of 5m = {span_days:.0f} days")


print("\ntwelvedata builds a paging request correctly")

captured = {}


def fake_get_json(url):
    captured["url"] = url
    return {"values": [{
        "datetime": "2026-01-05 12:00:00",
        "open": "1.1", "high": "1.2", "low": "1.0", "close": "1.15",
        "volume": "0",
    }]}


import os                                                            # noqa: E402
orig_json, orig_key = td._get_json, os.environ.get("TWELVEDATA_KEY")
td._get_json = staticmethod(fake_get_json)
os.environ["TWELVEDATA_KEY"] = "testkey"
try:
    td.fetch("EURUSD", "5m", count=5000)
    first = captured["url"]
    check("no end_date on the first page", "end_date" in first, False)
    check("timezone pinned to UTC, so naive stamps really are UTC",
          "timezone=UTC" in first, True)

    cursor = 1_767_600_000
    td.fetch("EURUSD", "5m", count=5000, before_ts=cursor)
    nxt = captured["url"]
    check("later pages send end_date", "end_date" in nxt, True)
    # Inclusive bound, so it must step back to avoid re-serving the cursor bar.
    check("...stepped back one second off the cursor",
          "2025-01-05+07%3A59%3A59" in nxt or "07%3A59%3A59" in nxt,
          True)
    check("outputsize never exceeds the vendor cap",
          "outputsize=5000" in nxt, True)
finally:
    td._get_json = orig_json
    if orig_key is None:
        os.environ.pop("TWELVEDATA_KEY", None)
    else:
        os.environ["TWELVEDATA_KEY"] = orig_key

print(f"\n{PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
