# crt-confluence

Multi-timeframe confluence tooling for the EURUSD CRT / Asian Range strategy.

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
web/index.html        the dashboard
web/chart.js          Lightweight Charts wiring + the zone primitive
web/vendor/           Lightweight Charts v5 (Apache 2.0) + its licence
assets/               Monsieur Pierre
tests/                hand-built bars with known answers
```

## Running it

```bash
# fake bars, just to see the dashboard work
python3 -m src.pipeline --synthetic --bars 12000 --out web/data.json

# real 5m bars: time,open,high,low,close[,volume]
python3 -m src.pipeline --csv data/eurusd_5m.csv --out web/data.json

# then
python3 -m http.server 8000      # open http://localhost:8000/web/
```

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

## Status

v1 in progress — detect and draw.
v2 — replay over history, count how often each confluence combination preceded the
documented targets. Out-of-sample only.
