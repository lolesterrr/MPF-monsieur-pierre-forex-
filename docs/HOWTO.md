# Using MPF

The one rule underneath everything: **the score tells you where to look, your
eyes decide.** It counts conditions from the strategy doc. The weights are
untested placeholders (RULEBOOK §10). Treat it as an index, not a verdict.

---

## The loop

1. Read `bias.stack`. No bias means no trades — your own doc says so.
2. Read the `confluences` list. Each row is a thing to go and verify.
3. Click **look →** on a row. The chart switches to that timeframe, scrolls to
   the moment, **marks the exact candle** with an arrow and a highlighted
   column, and the bar above names it: time, OHLC, and the level's price.
4. Decide with your own eyes whether the thing is real.
5. If you take it, log it. That is how the weights eventually get replaced
   with measured numbers instead of my guesses.

---

## Five ways a session goes

### 1. The clean one

```
bias.stack   bullish · 3 of 3 layers agree
confluences  13/15
  1d   CRT on 1d          bullish, range 1.01549–1.02248
  1h   PLP on 1h          1.01436, taken
  4h   4h fvg             sits within 5 pips of the PLP
  1h   Model #1 trigger   entry 1.01668, stop 1.01390
```

Click the 4H FVG. Chart lands on 4H with the zone drawn and the candle marked.
Is it a real three-bar gap, or three candles that happen not to overlap?

Click the Model #1 trigger. Lands on 1H. Is the entry candle what the doc
describes — a down-close candle that took out a low, with price closing back
above it?

If both hold up, size so the 27.8 pip stop is your 1R, and log it:

```bash
python3 -m src.journal log --symbol EURUSD --direction long \
  --entry 1.01668 --stop 1.01390 --score 13
```

### 2. The trap — a big score made of one thing

```
15m  15m order block   sits within 5 pips of the PLP
15m  15m order block   sits within 5 pips of the PLP
15m  15m swing         sits within 5 pips of the PLP
15m  15m swing         sits within 5 pips of the PLP
```

Four rows, 12/15, and when you click through them it is one cluster of chop
counted four times.

**This is the main failure mode of the whole tool.** The order block detector
casts deliberately wide because you said you would judge them. Go to the
`orderblock.queue` panel and **drop** the junk. They stop inflating the count.

A 12 made of one real level wearing four hats is weaker than a 7 made of three
separate ones.

### 3. The skip

```
weekly   W5 inside week — no bias        ·
daily    D5 outside day — skip           ·
1H       H1 profile unclear — skip       ·
```

Score 4/15, verdict `no bias — the doc says no trades`.

Nothing to do. The value here is not finding a trade, it is having something
that says no while you are bored at 2am.

### 4. The stale one

Red banner: `Data is 1h 20m old`, or `Forex is shut for the weekend`.

Everything below might look perfect. It is a photograph. The PLP was taken,
Model #1 armed, and price has done whatever it did since. The entry level on
screen could be 40 pips away by now.

Weekend: nothing to do, it is Saturday. Stale during market hours: fix the
feed before trusting anything on screen. Alerts already refuse to fire in this
state; the banner does the same job for your eyes.

### 5. The right idea, wrong moment

Score 11/15, everything clean. You click the Model #1 trigger and the focus
bar says the candle is from **six hours ago**. Price has already reached the
CRT 50%.

The tool reports that conditions were met. It does not know "too late". The
timestamp in the focus bar is yours to read.

Rule of thumb: if price is already past the first exit, the setup is spent.

---

## Reading the chart

| Cue | Means |
|---|---|
| Gold arrow + label | the exact candle the selected confluence refers to |
| Highlighted column | same candle, so you never count bars by eye |
| Shaded band | an FVG (gold) or order block (blue) zone |
| `4h` text on a band | which timeframe that level came from |
| Dashed lines | daily 50%, CRT exits |
| Dotted red | PLP, stop |

Timeframe tabs switch the candles; **the levels follow you across** and keep
their source-timeframe label. That is the point: see a 4H level while looking
at 5m candles.

The checkboxes hide level types. When the chart is a mess, turn swings off
first.

**strict OBs** is different from the others — it filters rather than hides.
It drops order blocks whose displacement never took out a prior swing, which
is roughly half of them. Weak ones carry a `weak` badge when strict is off, so
you can see what would disappear before committing to it.

Neither setting is "correct" yet. Run the backtest both ways and let the
out-of-sample number decide.

---

## The part that decides whether any of this is real

```bash
python3 -m src.journal stats
```

Prints expectancy grouped by confluence score. If your 12s and your 7s earn the
same, the score is noise and the weights need rewriting from the backtest's
per-condition breakdown.

```bash
curl 'localhost:8000/api/backtest?symbol=EURUSD&pages=8'
```

Out-of-sample only, costs charged, stop assumed first on ambiguous bars. On a
random walk it correctly reports roughly −0.03R, so it is not flattering you.

Until real data runs through both, every number in this app is a hypothesis.
