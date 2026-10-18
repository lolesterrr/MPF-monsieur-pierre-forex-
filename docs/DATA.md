# Getting data without a broker account

OANDA refused a Ugandan signup. That is not a bug and not worth fighting.

**OANDA is a broker.** Brokers need a licence in each country they accept
clients from, and that licensing is why signups get geo-blocked. It has nothing
to do with the API.

**Data vendors and public file servers are not brokers.** They sell or publish
numbers, so there is no per-country licensing and no geo-block. MPF now carries
four of them.

---

## Live vs historical — read this first

These are different problems and one provider does not solve both.

| Need | Use | Why |
|---|---|---|
| **Backtest, history** | Dukascopy | free, deep, no account |
| **Live chart, alerts** | Twelve Data | free key, real-time forex |

**Dukascopy is NOT live.** It publishes one file per *completed* hour, so the
current hour cannot exist yet. You are always looking at data at least an hour
old. For backtesting that is irrelevant. For reading a 5-minute chart it is
dangerous — the setup you are looking at has already played out.

MPF handles this for you rather than leaving it as a footgun:

- Every analysis carries `age_seconds` and a `stale` flag.
- The dashboard prints the data's age next to the instrument, in red past 15
  minutes, with a banner explaining why.
- **Alerts refuse to fire on stale data.** A notification about an hour-old
  setup is worse than silence.

The provider ordering already reflects the split: live requests try Twelve Data
first, `get_history` picks the deepest pager, which is Dukascopy.

If you only ever backtest, Dukascopy alone is genuinely enough. If you want a
live chart, get the free Twelve Data key as well.

---

## What to use, in order

### 1. Dukascopy — no account at all · **for history**

A public static file server. No signup, no key, no country check, and the
deepest free history of anything here — years of tick data.

```bash
python3 -m src.data.dukascopy --verify EURUSD     # do this FIRST
python3 -m src.data.dukascopy --symbol EURUSD --tf 5m --days 30 --out eurusd.csv
python3 -m src.wsgi                               # dashboard picks it up
```

Covers: 10 FX pairs, **XAUUSD and XAGUSD**, NAS100 / SPX500 / US30, oil.

**Verify before trusting it.** This is an undocumented binary endpoint. The
record layout comes from community reverse-engineering and sources disagree on
whether prices are int32-scaled or float32. MPF parses *both ways* and keeps
whichever lands inside a plausible price band for the instrument, raising
loudly if neither does — so a wrong-scale failure becomes an error instead of a
backtest built on garbage. `--verify` prints an hour of real quotes so you can
confirm with your own eyes.

**Trade-offs:**

- One HTTP request per hour of history, so 30 days is ~720 requests and takes a
  while. Free and deep, not fast.
- **Delayed by at least an hour** — see the section above. History only.

### 2. Twelve Data — free key, forex only · **for live**

Signup is open worldwide. Free tier is **8 requests/minute, 800/day**, which is
plenty for one person clicking a dashboard.

**Commodities are not on the free tier** — gold needs a paid plan. So this
covers EURUSD well and XAUUSD not at all unless you pay.

`TWELVEDATA_KEY` — twelvedata.com → sign up → API key.

### 3. Yahoo — no key, and the only free source with real futures

Already wired. Carries NQ/ES/YM/RTY, GC, SI, CL, DXY.

Same three warnings as before: undocumented endpoint, ~60 days of 5m history,
and continuous contracts splice months together so the detectors will find
FVGs inside roll gaps. Those levels are artifacts.

### 4. Alpha Vantage — free key, fallback only

Open worldwide. The free allowance is small (documented as 25 requests/day when
this was written — check the current figure). No metals on free.

`ALPHAVANTAGE_KEY` — alphavantage.co/support/#api-key.

---

## Other brokers, if you still want a live feed

Broker availability in Uganda changes and I can't confirm any of it from here,
so treat this as a list to check rather than a recommendation:

- **Deriv** has a documented WebSocket API and accepts clients in many African
  countries. Worth checking first.
- **Exness**, **FXTM**, **HotForex/HFM** and **XM** market to East Africa, but
  API access varies and some offer only MT4/MT5 rather than REST.
- **MetaTrader 4/5** is the realistic fallback: many brokers that accept Ugandan
  clients offer MT5, and MT5 has a Python package that can export bars to CSV.
  MPF's `load_csv` takes those directly.

If you go the MT5 route, export to `time,open,high,low,close,volume` and feed it
straight in:

```bash
python3 -m src.pipeline --csv your_export.csv --out web/data.json
```

**Check any broker's current regulatory status before depositing money.** That
is a separate question from data access, and MPF needs no deposit at all.

---

## The honest recommendation

For what MPF actually does — read candles, draw levels, backtest — **Dukascopy
alone is enough, and it needs nothing from you**. No account, no key, no
country check, and more history than any free broker feed would give you.

Verify the decode once, pull 30 days, and run the backtest. That was always the
real blocker, and it was never actually OANDA.

---

## How history gets fetched, and the 504 that taught us why

The replay engine was never the problem — 40,000 bars produce 113 trades
across a clean train/test split in under a second. Every failure was in
getting data to it. Pressing **run backtest** on a deployed instance returned
HTTP 504 and nothing else. Three causes, each invisible on its own:

**1. The chooser asked the wrong question.** `get_history` picked its pager
with `max(pagers, key=max_per_request)` — which reads as "go as deep as you
can" and behaves as "always pick Dukascopy", because Dukascopy advertises
100,000 bars against everyone else's 5,000. Dukascopy also issues one HTTP
request *per hour of history*, so a six-page pull was **50,000 requests** from
a function with 60 seconds to live. The size of a page was never the right
question; whether the provider can answer inside its budget is.

**2. Twelve Data could not page.** `supports_paging` was False, so the one
provider that could have answered was never eligible. With Dukascopy out it
would have fallen through to a single 5,000-bar call — 17 days of 5m, a
handful of trades, an honest "too few trades".

**3. An unconfigured provider could still win.** OANDA ties with Twelve Data
on page size and sorts first, so once the slow pager was excluded the chooser
picked OANDA and the backtest died on `OANDA_TOKEN is not set` — a missing key
surfacing as a data failure. `get_bars` had always skipped unconfigured
providers; this path never did. Only an end-to-end run through the real route
exposed it.

**Now:** Twelve Data pages with `end_date` (and pins `timezone=UTC`, so the
naive timestamps it returns really are UTC rather than accidentally so).
`pick_pager` considers only providers that are **configured** and, for an
interactive call, **`live_capable`** — and raises a message naming the slow
provider and pointing here, rather than hanging. `get_history` also takes a
`budget_seconds` so a slow page cannot run out the clock.

Six pages of 5m through Twelve Data is about **104 days** — enough for a real
out-of-sample run. End to end through the WSGI route with the vendor stubbed:
HTTP 200 in 0.3s, 6 requests.

Free-tier note: 8 requests/minute. Six pages is fine; the route's cap of 12
could brush the limit.

Dukascopy stays the right answer for deep history pulled **offline**, where
there is no 60-second ceiling — ask for it by name and `pick_pager` honours
it:

```bash
python3 -m src.data.dukascopy --verify EURUSD     # eyeball the decode first
# then pull locally, write a CSV, and feed it in:
python3 -m src.pipeline --csv eurusd_5m.csv --out web/data.json
```

Note for anyone running this inside a sandboxed agent environment:
`datafeed.dukascopy.com` and `api.twelvedata.com` are usually not on the
egress allowlist, so neither can be reached from there. That is a property of
the sandbox, not of the providers.
