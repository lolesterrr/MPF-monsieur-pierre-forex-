# MPF CRT — MT5 Expert Advisor

`MPF_CRT.mq5` runs MPF's C3 entry model in the MT5 Strategy Tester, so it can
be tested on years of your broker's history. The rules are the ones in
`rules/RULEBOOK.md` §3, §5-§7, all confirmed by the strategy's author.

## Install

1. MT5 → File → Open Data Folder → `MQL5/Experts/` → copy `MPF_CRT.mq5` there.
2. Open it in MetaEditor (F4) and press **Compile** (F7). It should report
   0 errors. If it doesn't, copy the errors back to Claude.

## Run a backtest

Strategy Tester (Ctrl+R):

| Setting | Value |
|---|---|
| Expert | MPF_CRT |
| Symbol | EURUSD |
| Timeframe | M5 (any works; the EA reads M5/M15/H1 itself) |
| Dates | e.g. last 2-3 years |
| Modelling | **Every tick based on real ticks** (best) or 1 minute OHLC |
| Deposit / leverage | your real account values |

Download history first (View → Symbols → EURUSD → Bars, or just let the
tester fetch it).

## The one setting that matters: the server clock

Everything in this strategy happens on **New York time** (17:00 NY day open,
00:00 NY C3 mark, 4H candles at 01/05/09/13 NY). The EA has to know how your
broker's clock relates to New York:

- **CLOCK_NY_CLOSE** (default) — most brokers. Server time is GMT+2 in winter,
  GMT+3 in summer, so the daily candle opens at server 00:00 = 17:00 NY and
  there are exactly 5 daily candles a week (no Sunday candle).
- **CLOCK_EU_DST** — GMT+2/+3 but switching on European dates.
- **CLOCK_FIXED** + `ServerUTCOffset` — a broker on a fixed offset, e.g. UTC+0.

Check: the Journal prints `server ... is ... New York` when the EA starts.
Compare with a NY clock. If it's wrong, every rule is applied at the wrong
hour.

## Reading the result

- The tester's report is the performance: profit, drawdown, win rate,
  profit factor, trades.
- The **Journal** ends with the funnel — where CRTs dropped out:
  `MPF funnel | CRTs … | 4H off-hours … | against bias … | no PLP/PD array …`
  `| not reached in C3 … | no Model#1 in C3 … | under 2.0R … | limit unfilled … | TRADED …`

## Inputs

| Input | Default | Meaning |
|---|---|---|
| TradeDailyCRT / Trade4HCRT | true | which CRTs to trade |
| UsePDArrayEntry | true | enter off a fresh PD array when there's no PLP |
| MinRR | 2.0 | T1 must be at least this many R |
| StopBufferPips | 2 | beyond the Model #1 candle |
| LookbackBars | 48 | PLP / PD array search |
| PdReachPips | 20 | 1H PD array "resting below" the PLP → 5M entry |
| T1ClosePercent | 60 | closed at T1 (CRT 50%); the rest runs to the CRT extreme |
| TimeoutHours | 24 | anything still open is closed |
| RiskPercent / FixedLots | 1% / 0 | position size |

## Not in the EA (same as MPF's own backtest)

Weekly draw as a bias window, 1D PD-array invalidation, SMT exit, C2 entries.
See RULEBOOK §7.1.

## Testing it fairly

Decide what you'll look at **before** running, test the whole period once,
and don't tune inputs on the same years you then quote. If you change a rule,
check it on years you haven't looked at yet.
