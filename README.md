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
src/core/             types, bias, sessions
src/detectors/        swings, levels (FVG + OB), CRT, Model #1
src/data/             feed adapters (TBD)
tests/                hand-built bars with known answers
```

## Running the tests

```bash
python3 tests/test_detectors.py
```

No dependencies. Plain stdlib, Python 3.11+ (uses `zoneinfo`).

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
