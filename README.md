# MPF — Monsieur Pierre's Forex

Multi-timeframe confluence tooling for the EURUSD CRT / Asian Range strategy.

Not an exchange and not a broker. It reads candles and draws levels; it places
no orders and holds no money.

The point: make higher-timeframe → lower-timeframe analysis legible at a glance,
instead of flipping through six charts and holding it all in your head.

## What this is

A detection layer. It finds and draws the things the strategy cares about —
bias, CRTs, FVGs, order block candidates, PLPs, Asian ranges, Model #1 triggers —
and shows which of them line up.

## What this is not

It does not tell you to take a trade. It does not claim an edge. A confluence
score is a count of conditions met, nothing more, and the weights in
`rules/RULEBOOK.md` §10 are placeholders until they survive out-of-sample data.

## Layout

```
rules/RULEBOOK.md     the spec — code implements this, never the reverse
src/core/             types, bias, sessions, confluence engine
src/detectors/        swings, levels (FVG + OB), CRT, Model #1
src/data/feed.py      CSV loader, resampler, synthetic generator
src/pipeline.py       runs the full pass, writes web/data.json
src/data/providers.py one interface, several vendors
src/wsgi.py           the web app (local / Vercel / Render)
app.py                deploy entrypoint
docs/OANDA.md         getting a free token, step by step
docs/DEPLOY.md        hosting, and where keys must never go
web/index.html        the dashboard
web/chart.js          Lightweight Charts wiring + the zone primitive
web/vendor/           Lightweight Charts v5 (Apache 2.0) + its licence
assets/               Monsieur Pierre
tests/                hand-built bars with known answers
```

## Data

No single vendor covers forex, futures and metals cheaply, so `src/data/providers.py`
puts several behind one interface and the registry picks per instrument. Swapping
vendor later is one class, not a rewrite.

```bash
export OANDA_TOKEN=...        # free practice account, forex + metals
export TWELVEDATA_KEY=...     # optional second source
python3 -m src.server         # http://localhost:8000
```

| Provider | Covers | Key | Notes |
|---|---|---|---|
| **OANDA v20** | 10 FX pairs, XAU/XAG, index CFDs | free practice account | Best free real feed. 5000 candles/request. Index CFDs are **not** the CME contracts |
| **Twelve Data** | FX, XAU/XAG | free tier | Small allowance; check current limits |
| **Yahoo** | FX, **NQ/ES/YM/RTY**, GC, SI, CL, DXY | none | Only free source with real futures. See the warning below |

Credentials come from environment variables. Nothing is committed.

### On the futures problem

Real CME data (NQ, ES) is licensed and priced accordingly — Databento starts at
$199/mo for historical only, with live on a much higher annual tier; Polygon's
real-time tier is $199/mo and its cheaper tiers are 15-minute delayed. There is
no free legitimate live CME feed.

Yahoo's `=F` tickers are the free workaround, with three catches worth knowing
before you trust a backtest built on them:

1. It is an **undocumented endpoint**, not a supported API. It can change or
   start refusing requests without notice.
2. Intraday history is shallow — roughly 60 days at 5m, less at 1m.
3. **Continuous contracts splice contract months together.** The splice creates
   price jumps that are not market moves, and the FVG and order block detectors
   will happily find levels inside a roll gap. Those levels are artifacts.

Fine for personal research. Not something to build a product on.

For forex and gold — which is most of the strategy doc — OANDA's practice
account is a real feed with no catch, and that is the recommended path.

## Running it

**Web (the normal way).** Instrument and timeframe pickers, live fetch:

```bash
python3 -m src.data.dukascopy --verify EURUSD   # no account needed
python3 -m src.wsgi                             # http://localhost:8000
```

Five data providers behind one interface. **Dukascopy needs no account, no key
and no country check** — which matters, because OANDA is a broker and brokers
geo-block signups. See `docs/DATA.md`.

Endpoints: `/api/catalogue`, `/api/analyze?symbol=&tf=`, `/api/health`.
Add `?synthetic=1` to see it work with no keys at all.

One WSGI app, so the same code runs locally, on Vercel and on Render —
see `docs/DEPLOY.md`.

**Deploying needs `MPF_TOKEN` set.** Without it MPF answers only on localhost,
on purpose: a public instance with no key lets anyone call OANDA with your
token. `docs/DEPLOY.md` explains why that, and not rate limiting, is the
protection that matters.

**CLI (one-off, writes a static JSON).**

```bash
python3 -m src.pipeline --synthetic --bars 12000 --out web/data.json
python3 -m src.pipeline --csv data/eurusd_5m.csv --out web/data.json
```

The dashboard falls back to that file when opened without the server.

`--anchor-hour-utc` sets the daily open (17:00 NY = 21 during EDT, 22 during EST).
If your feed already gives daily bars, use those instead of resampling.

## Tests

```bash
python3 tests/test_detectors.py    # 43
python3 tests/test_confluence.py   # 24
```

No dependencies. Plain stdlib, Python 3.11+ (uses `zoneinfo`).

## The dashboard

Reads `web/data.json`. Panels: the bias stack (weekly → daily → 1H, and whether
they actually agree), the confluence count, a 1H chart with every timeframe's
levels overlaid, the active CRT, PLP alignment, the Asian range, the Model #1
trigger, and a queue of order block candidates for you to keep or drop.

### Chart

[Lightweight Charts](https://github.com/tradingview/lightweight-charts) v5,
Apache 2.0, vendored at `web/vendor/` so there is no build step and no CDN
dependency. Licence kept beside it; the TradingView attribution mark stays on
the chart, which is how the licence is satisfied.

v5 notes, since most tutorials online are still v4:

- Series are created with `chart.addSeries(CandlestickSeries, opts)`, not the
  v4 `chart.addCandlestickSeries(opts)`.
- The standalone build exposes everything on `window.LightweightCharts`.
- The library has no box/rectangle, so FVG and order block zones are drawn by a
  **series primitive** (`web/chart.js`) that gets the chart's own coordinate
  converters — zones stay pinned to price and time through zoom and pan.
- `localization.locale` is pinned to `en-US`. The library formats axis labels
  through `Intl`, and an unusual system locale throws mid-render and silently
  blanks the chart.

Horizontal markers (daily 50%, CRT exits, PLP, entry, stop, Asian range edges)
are real price lines, so the library places and de-collides their axis labels.

Order block verdicts save to `localStorage` on your machine. They are per-browser
and are not read back by the pipeline yet — the plan is to use them to measure how
well the 1.5× ATR filter matches your eye.

## Not wired up yet

- **SMT** needs GBPUSD and DXY bars alongside EURUSD. The detector is written and
  tested (`check_smt`, including the DXY inversion); the pipeline passes `None`
  until there is a second feed.
- **A data feed.** The synthetic generator is a random walk. It exists so the UI
  can be built, and nothing it produces means anything.

## Rulebook first

`rules/RULEBOOK.md` is the single source of truth. Every `[D]` in it is a default
I chose, not a rule Isaiah specified — those are the ones to argue with. If the
code and the rulebook disagree, the rulebook wins and the code is a bug.

Three gaps the strategy doc flags itself and still owes an answer:

1. What invalidates a bias mid-week / mid-day
2. When to stop taking new CRTs before the target is reached
3. How far back the Model #1 search may go before it's a no-setup

## No-lookahead

Every detector reads only bars at or before the one it is evaluating. Swings carry
a `confirmed_index` separate from the index they formed at, because a swing is not
knowable until `n` bars later. This is not pedantry — it is the single most common
reason a confluence indicator backtests beautifully and dies live.

## Chart layer

Undecided. Two candidates, both from TradingView:

- **Lightweight Charts** — Apache 2.0, genuinely open source, ~45KB. Rendering only;
  zones go in through the primitives API. Every drawing tool is yours to build.
- **Advanced Charts** — free but not open source, access by application. Ships the
  familiar drawing tools and UI; custom studies via their JS API, data via their
  datafeed interface.

The Python layer here is chart-agnostic, so this can be decided late.

## Using it

`docs/HOWTO.md` walks five real sessions — the clean one, the trap, the skip,
the stale one, and the right-idea-wrong-moment. Start there.

The short version: the score tells you where to look, your eyes decide.
Clicking a confluence marks the exact candle, so you never deduce it from
price levels.

## Backtest

```bash
curl 'localhost:8000/api/backtest?symbol=EURUSD&pages=8'
# or press "run" in the dashboard's backtest panel
```

Replays the rules bar by bar. Three things make it honest rather than flattering:

- **Costs are charged.** Spread and slippage, both ways. A 27-pip stop with 1.2
  pips of cost is ~4% of the risk.
- **Ambiguous bars assume the stop.** When one bar's range spans both the stop
  and the target, OHLC cannot say which came first, so the worse one is assumed.
  Without this rule every range strategy backtests beautifully.
- **The train/test split is chronological and not optional.** `evaluate()` will
  not report a tuned-on number as if it were out-of-sample.

It also names the three ways a backtest lies: too few trades, a train/test
collapse, and a result carried by two or three outliers.

**Sanity check:** on synthetic data — a random walk — it reports about −0.03R
per trade. That is what a random walk should give after costs. A backtester
that found an edge in noise would be broken.

## Alerts and journal

```bash
python3 -m src.alerts --watch 300        # Telegram or stdout
python3 -m src.journal log --symbol EURUSD --direction long \
    --entry 1.0845 --stop 1.0820 --score 11
python3 -m src.journal stats
```

The backtest says what the rules did. The journal says what you did. The gap
between them is execution, and it is usually where the money goes.

## Status

- **v1 — detect and draw.** Done.
- **v2 — out-of-sample replay.** Done, waiting on real data.
- **Next:** a token (see `docs/SETUP.md`), then run the backtest on real EURUSD
  and find out whether any of this holds up.
