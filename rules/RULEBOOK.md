# CRT & Asian Range — Machine Rulebook

**Pair:** EURUSD only
**Source:** `EURUSD_Strategy_CRT_and_Asian_Range.pdf` (Isaiah, 2026-10-12)
**Status:** v0.1 — defaults marked `[D]` are my choice, not yours. Change any of them.

This file is the single source of truth. Code implements this document; it does not
invent rules. If the code and this file disagree, this file wins and the code is a bug.

---

## 0. Conventions

| Item | Rule |
|---|---|
| Timezone (internal) | All timestamps stored in UTC |
| Timezone (display/sessions) | America/New_York, DST-aware |
| Daily/weekly candle open `[D]` | 17:00 New York (forex convention) |
| Weekly candle | Opens Sunday 17:00 NY, closes Friday 17:00 NY |
| "Close above/below X" | Candle **body** close, not wick |
| "Wick above/below X" | Candle high/low exceeds X while the close does not |
| Pip (EURUSD) | 0.0001 |
| Bar indexing | 0 = oldest. "Previous candle" = index − 1 |

**No-lookahead rule.** Every detector may only read bars at or before the bar it is
evaluating. Any detector that reads a future bar is a bug, not a feature. This is the
single most common way a confluence indicator looks profitable and isn't.

---

## 1. Swing points

A **swing high** at bar `i` requires:
- `high[i] > high[i-1]` and `high[i] > high[i-2]`
- `high[i] > high[i+1]` and `high[i] > high[i+2]`

Mirror for swing low with lows. Lookback/lookforward `n = 2` `[D]`.

**Consequence:** a swing is only *confirmed* 2 bars after it forms. The detector marks
it at bar `i` but it is not tradeable until bar `i+2` closes. Every downstream rule uses
the confirmed timestamp, never the swing's own timestamp.

A swing is **taken / swept** when a later bar's high exceeds a swing high (or low
below a swing low), by wick or body.

A swing is **mitigated** when a later bar **closes** beyond it.

---

## 2. Levels — the pool

Three level types feed one shared pool. Every level carries:
`{type, direction, top, bottom, created_at, status, timeframe}`

`status` ∈ `{valid, swept, invalidated}`

### 2.1 FVG (fair value gap)

Three consecutive bars `i-2, i-1, i`:
- **Bullish FVG:** `low[i] > high[i-2]`. Zone = `high[i-2]` → `low[i]`
- **Bearish FVG:** `high[i] < low[i-2]`. Zone = `high[i]` → `low[i-2]`

Minimum size: none `[D]`
Invalidation: a bar **closes** fully through the zone `[D]`
Partial fill: tracked but does not invalidate `[D]`

### 2.2 Order block candidate

Per your call — cast wide, you filter by eye.

1. **Displacement bar:** `range[i] >= 1.5 × ATR20[i-1]`
   where `range = high − low`, `ATR20` = simple average of the prior 20 bar ranges.
2. **Bearish OB candidate:** displacement bar closes down → the nearest preceding
   **up-close** bar (`close > open`) is the candidate. Search back max 10 bars `[D]`.
3. **Bullish OB candidate:** displacement bar closes up → the nearest preceding
   **down-close** bar is the candidate.
4. **Zone:** full candle range, `high` → `low`.
5. **Invalidation:** a bar **closes** through the zone (below bottom for bullish,
   above top for bearish).
5a. **The rule is deliberately loose.** It tests two things only: the candle
   closed against the following move, and that move was >= 1.5x ATR20. It does
   **not** check that the displacement broke structure, that the candle sits at
   a swing, or that anything was left unmitigated. That is why the dashboard
   prints the matched rule next to every candidate — so a weak one is visible
   as weak rather than asserted as an "order block".

   **Strict mode** does exactly that: every candidate records whether its
   displacement took out a confirmed prior swing (`broke_structure`), and the
   dashboard's "strict OBs" switch hides the ones that did not. Both sets are
   always in the payload, so flipping the switch filters rather than refetches
   and you can compare the two instantly.

   On synthetic data roughly **47%** of candidates break structure, so strict
   mode about halves them. Whether that half is the better half is a question
   for the backtest, not for me — run it both ways with `min_score` held
   constant and compare out-of-sample expectancy.

6. **Sweep without close does NOT invalidate.** Wick-through is expected behaviour.
7. Each candidate gets a stable `id` so you can accept/reject it and the choice persists.

### 2.3 Swing high / low

Any confirmed swing from §1 is a level with zero thickness.

---

## 3. Bias

### 3.1 Weekly bias (Model 1)

Evaluated on the **closed** previous week, applied to the current week.

Precedence, first match wins `[D]`:

| # | Condition | Bias |
|---|---|---|
| 1 | Week closed **below** previous week's **low** | Bearish |
| 2 | Week closed **above** previous week's **high** | Bullish |
| 3 | Week **wicked above** previous week's high, closed back inside | Bearish |
| 4 | Week **wicked below** previous week's low, closed back inside | Bullish |
| 5 | None of the above | **None — no trades** |

> **Gap in your doc.** It lists "close below the previous week's candle" (ambiguous:
> below its low, or its close?), "wick above previous high → bearish", and "close above
> previous high → bullish". It gives no bullish wick rule. I added #4 as the mirror of #3.
> It also doesn't say what wins when a week both wicks above the high *and* closes below
> the low — an outside week. Rule #1 firing first is my `[D]`.

### 3.2 Daily bias (Model 2, for the *next* day)

Evaluated on the **closed** day, applied to the following day.

| # | Condition | Next-day bias |
|---|---|---|
| 1 | Closed **above** previous day's high | Bullish |
| 2 | Closed **below** previous day's low | Bearish |
| 3 | **Wicked above** previous day's high, closed back inside | Bearish |
| 4 | **Wicked below** previous day's low, closed back inside | Bullish |
| 5 | Both sides wicked, closed inside (outside day) | **None — skip** `[D]` |
| 6 | Inside day (neither side touched) | **None — skip** `[D]` |

### 3.3 1H alignment check (Model 2 gate)

Over the last `N = 6` confirmed 1H swings `[D]`:
- **Bullish profile:** last 2 swing highs rising AND last 2 swing lows rising
- **Bearish profile:** last 2 swing lows falling AND last 2 swing highs falling
- Anything else: **unclear → skip the trade**

> Your doc says "highs broken and lows rejected". I translated that to higher-highs /
> higher-lows because "rejected" has no numeric form. If you mean something stricter —
> e.g. a low must be wicked and closed back above — say so and I'll change it.

---

## 4. Premium / discount

- Daily range = current day's high and low **so far** (live), or previous day's
  high/low when flat `[D]`
- `eq = (high + low) / 2`
- Price `> eq` → **premium**; `< eq` → **discount**
- Valid target: a level on the **opposite** side of `eq` from current price

---

## 5. CRT (Model 1)

Evaluated on **Daily** and **4H** only, in the bias direction only.

### 5.1 Structure `[D]`

Three consecutive bars:
- **C1** — the range bar. `range_high = high[C1]`, `range_low = low[C1]`
- **C2** — the manipulation bar. Wicks beyond one side of C1's range and
  **closes back inside** C1's range
- **C3** — the trade bar. This is the only bar traded.

**Bearish CRT:** C2 wicks above `range_high`, closes inside.
**Bullish CRT:** C2 wicks below `range_low`, closes inside.

Direction must match the active bias or the CRT is ignored.

### 5.1.1 When a CRT is reported — C2's close, not C3's

**The pattern is complete the moment C2 closes.** C1 gives the range, C2 sweeps
it and closes back inside, and both are shut bars by then. C3 is the
*consequence*, not the evidence — nothing about detection needs it.

This matters because getting it wrong is invisible. The detector originally
required a C3 bar to *exist in the array* before reporting, and `resample()`
drops the forming bar, so "exists" meant "already closed". The freshest CRT
the dashboard could ever show was one whose trade candle was over — a full day
late on D1. Both halves looked correct on their own.

So C3's timestamp is **projected** from C2 rather than read from a bar, and
rolled forward past the weekend (`market_hours.next_open`) — Friday's daily
candle plus 24h is Saturday, but the next daily candle opens Sunday 17:00 NY.

A CRT carries `c3_pending`:

| `c3_pending` | Means | Use |
|---|---|---|
| `True` | C2 closed, C3 has not | **The only kind you can still trade.** Verify the sweep on C2. |
| `False` | C3 is a closed bar | History. For study or backtest, not for taking. |

The backtester skips pending CRTs — there is no C3 bar to replay — which is
correct, and is *not* the same as hiding them from the dashboard. Note the
asymmetry this used to create: the backtest scored a strategy acting at C3's
**open** while the UI could not reveal the setup until C3's **close**. The
numbers measured something the dashboard was incapable of executing.

C3's OHLC is never consulted either way. No lookahead.

### 5.2 Derived levels

- `crt_50 = (range_high + range_low) / 2`
- Bearish opposing range = `range_low`; bullish = `range_high`

### 5.3 Tier map

| CRT timeframe | PLP timeframe |
|---|---|
| Daily | 1H |
| 4H | 15M |

---

## 6. PLP (pre-open liquidity pool)

> Your doc defines PLP by *outcome* ("looks like a structure shift but is fake"),
> which is only knowable after the fact. Code needs a *before* definition.

**Working definition `[D]`:** the most recent **unswept confirmed swing** on the
PLP timeframe, on the side opposite the bias, formed before C3 opens.

- Bearish bias → the nearest unswept **swing high** *above* current price
- Bullish bias → the nearest unswept **swing low** *below* current price

**PLP taken:** price trades through it (wick is enough).

Marked at C3 open. If no qualifying swing exists in the last `48` PLP bars `[D]`,
no PLP → no setup.

---

## 7. Model #1 (entry trigger)

Searched on the **PLP timeframe**, only **after** the PLP has been taken.

**Bearish:**
1. Find the last bar that is **up-close** (`close > open`) AND whose `high` exceeded
   the previous bar's `high`
2. Wait for a subsequent bar to **close below** that bar's `low`
3. On that close → entry order at that bar's **low** `[D]` (limit, expecting retrace)
4. Stop loss: above that bar's **high** `[D]`

**Bullish:** mirror — last down-close bar that took out a low; close above its high;
entry at its high; stop below its low.

**Validity window `[D]`:** the entry level stays live until C3 closes (Model 1) or
until the session's target/stop is hit (Model 2). Then it expires.

---

## 8. SMT divergence

Comparison symbols: **GBPUSD** and **DXY**. EURUSD is the only traded symbol.

**Bullish SMT `[D]`:** EURUSD makes a lower low than its previous confirmed swing low,
while GBPUSD does **not** make a lower low (or DXY does **not** make a higher high)
within a `±3 bar` window of the EURUSD swing.

**Bearish SMT:** mirror.

Evaluated on the **PLP timeframe** `[D]`.

Required for Model 2 Scenario A. Optional (probability booster) for Scenario B.

---

## 9. Model 2 — Asian Range

### 9.1 Range

Window **20:00–00:00 New York**. Mark `asia_high`, `asia_low`, `asia_50`.
Range is frozen at 00:00 NY.

### 9.2 Scenario A — false break

1. Range high or low is swept (wick beyond) and price **closes back inside** `[D]`
2. SMT **required** — at the daily PLP, at the prior day's bias-setting level, or at
   the Asian range itself
3. Find Model #1 → entry
4. Stop: beyond the entry candle
5. T1 = `asia_50`, close 60%. T2 = opposing end of range, close 40%
6. **No breakeven**

### 9.3 Scenario B — true break

1. A **5-minute close** beyond `asia_high` (bullish) or `asia_low` (bearish)
2. SMT optional
3. Mark `asia_50`. Require a 1H or 4H swing within **5 pips** `[D]` of it
4. Wait for price to return to `asia_50`
5. Find Model #1 → entry
6. Stop: beyond the entry candle
7. **One target:** the swing high formed after the true break (swing low if bearish)
8. **No breakeven**

### 9.4 Highest-probability flag

Raise a flag when the daily PLP sits within **5 pips** `[D]` of `asia_high` or `asia_low`.

---

## 10. Confluence scoring

The indicator does not say "take this trade". It shows which conditions are met.

| Check | Weight `[D]` |
|---|---|
| Weekly bias present and matches setup direction | 2 |
| Daily bias matches | 2 |
| 1H profile aligns | 1 |
| Price on correct side of daily 50% | 1 |
| Valid target exists in discount/premium opposite | 1 |
| CRT present on D or 4H in bias direction | 2 |
| PLP marked and taken | 1 |
| PLP aligns (≤5 pips) with an HTF level | 2 |
| SMT confirmed | 2 |
| Model #1 candle identified | 1 |

Displayed as `score / max` plus the per-check breakdown. **Weights are arbitrary
placeholders.** They mean nothing until backtested. Do not treat the number as an edge.

---

## 10.5 Research notes — where your doc differs from the common teaching

Checked against public CRT write-ups (LuxAlgo's concept library, ICT lecture material).
Your doc wins on every disagreement; these are logged so the difference is deliberate.

| Topic | Common teaching | Your doc | Code follows |
|---|---|---|---|
| Stop placement | Beyond **C2's** extreme (the manipulation candle) | Beyond the **entry candle** (Model #1) | Your doc |
| C1 range | High/low **including wicks** | unstated | wicks — agrees |
| C2 close | Must close back inside; a close beyond = acceptance, CRT void | unstated | close inside required — agrees |
| T1 / T2 | C1's 50%, then C1's opposite extreme | same | agrees |
| Entry timing | LTF, 15m–1H against a daily range | 1H for daily, 15M for 4H | agrees |

**On stops.** Yours is tighter than the common version. Tighter stop, better R per trade,
more stop-outs. Worth measuring both in v2 rather than assuming — it's a one-line change.

**On PLP.** The concept your doc describes — a high/low taken out that looks like a
structure shift but is a liquidity grab — is what ICT material calls a *liquidity purge
and revert* (also turtle soup). That is a description of what price did afterwards, so
it stays un-codeable as stated. §6's "nearest unswept swing on the opposite side" is the
best *before-the-fact* proxy, and it is the right shape: a resting pool of stops.

**Caveat worth repeating.** CRT is taught, not published. There is no independent study
testing it, and every teacher's version differs. That is exactly why §12's out-of-sample
step is not optional.

## 11. The gaps your doc flags — now filled with defaults

Implemented in `src/core/lifecycle.py`. Every number here is `[D]`; these are the
most likely to be wrong because your doc gave no guidance at all.

### 11.1 Bias invalidation

**A close beyond the opposite reference level kills the bias.** Reference levels
are the ones the bias was read from — the previous week's high/low for a weekly
bias, the previous day's for a daily one.

A wick beyond does **not** invalidate, because a wick is exactly what the
strategy expects (§3.1 rule 3 sets a bearish bias *from* a wick above). What says
the read was wrong is price going on to close above it anyway.

Alternatives, if you disagree: close beyond by N pips; two consecutive closes;
or invalidate only when the opposite bias fires.

### 11.2 When to stop taking CRTs toward one target

Three independent stops, whichever comes first:

| Stop | Default `[D]` |
|---|---|
| Target reached (wick counts) | — |
| Attempts at one target | 3 |
| Consecutive losses toward one target | 2 |

Your doc says "keep taking third-candle CRTs until the target is reached" with no
cap. Unlimited is almost certainly not what you meant: after two losers toward
the same target the read is wrong, not unlucky.

### 11.3 Model #1 search depth

20 bars `[D]`. Beyond that it is a no-setup rather than a stale candle.

### 11.4 Order block acceptance

The dashboard queue stores your keep/drop calls per browser. `src/backtest`
can later measure how well the 1.5× ATR filter matches your eye — that is the
point of collecting them.

---

## 12. Scope

**v1** — detect and draw. No signals, no auto-trading, no P&L claims.
**v2** — replay over history, count how often each confluence combination preceded the
documented targets being hit. Out-of-sample only.

Nothing in this repo is trading advice, and a confluence count is not an edge until it
survives data it was never tuned on.
