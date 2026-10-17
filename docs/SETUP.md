# What MPF needs from you

Everything is built. These are the accounts and keys only you can create.
In priority order — the first one unblocks everything else.

---

## 1. OANDA token · REQUIRED · ~10 min · free

Without it MPF can only show synthetic data, which is a random walk and means
nothing.

Full walkthrough: **`docs/OANDA.md`**. Short version:

1. oanda.com → register → choose **Practice**, not Live
2. Sign in → **My Account → Manage API Access** → Generate
3. `export OANDA_TOKEN=...`

No card, no deposit. Practice accounts get the same market data as live ones.

```bash
cp .env.example .env     # fill it in
export $(grep -v '^#' .env | xargs)
python3 -m src.wsgi      # http://localhost:8000
```

---

## 2. MPF_TOKEN · REQUIRED TO DEPLOY · 1 min · free

A shared key protecting `/api/`. Without it MPF answers only on localhost —
deliberately, because a public instance with no key lets anyone call OANDA with
your token, and that token class can place trades.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it in the host's environment variables. Details and the reasoning:
**`docs/DEPLOY.md`**.

---

## 3. Vercel account · to deploy · ~5 min · free

Push the repo, import it, paste the environment variables, deploy. Config is
already in the repo (`vercel.json`, `.python-version`).

Free Hobby tier is non-commercial — fine for personal use.

---

## 4. Telegram bot · OPTIONAL · ~5 min · free

Only if you want alerts pushed to your phone. Without it `src/alerts.py` prints
to the terminal and nothing breaks.

1. Message **@BotFather** on Telegram → `/newbot` → copy the token
2. Message your new bot once (it can't message you first)
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`
4. Set `MPF_TELEGRAM_TOKEN` and `MPF_TELEGRAM_CHAT_ID`

```bash
python3 -m src.alerts --watch 300
```

---

## 5. Twelve Data key · OPTIONAL · free tier

A second source for forex and metals, used when OANDA doesn't carry something
or fails. twelvedata.com → sign up → API key → `TWELVEDATA_KEY`.

---

## 6. Decisions only you can make

These are in the code as defaults marked `[D]`. They change behaviour, not
presentation. The rulebook explains each one's reasoning so you can disagree
precisely.

| Decision | Current default | Where |
|---|---|---|
| What a PLP is, before the fact | nearest unswept swing on the bias side | §6 |
| What "1H highs broken, lows rejected" means numerically | higher-highs + higher-lows | §3.3 |
| What invalidates a bias mid-period | a **close** beyond the opposite reference | §11.1 |
| How many CRT attempts per target | 3, or 2 consecutive losses | §11.2 |
| Order block displacement threshold | 1.5× the 20-bar average range | §2.2 |
| Stop placement | beyond the entry candle (your doc) vs beyond C2 (common teaching) | §10.5 |
| Confluence weights | arbitrary placeholders | §10 |

The weights are the least important — they are untested numbers and the
backtest's per-condition breakdown is how you'd replace them with measured ones.

---

## What you do NOT need

- **A broker account with money.** MPF places no orders.
- **A paid data feed**, unless you want real CME futures (NQ/ES), which start
  around $199/mo. Forex and gold are free via OANDA.
- **A domain.** Vercel gives you a URL.
- **A database.** Everything is files and environment variables.

---

## Once the token is in

```bash
python3 -m src.wsgi                  # dashboard
python3 -m src.alerts                # one alert scan
python3 -m src.journal stats         # your own results
curl 'localhost:8000/api/backtest?symbol=EURUSD&pages=8'
```

Then the real question: **does the backtest show anything out-of-sample?**
On synthetic data it correctly reports about -0.03R, which is what a random
walk should give after costs. If real EURUSD comes back similar, the honest
conclusion is that these rules as written have no edge — and that is worth
knowing before you trade them, not after.
