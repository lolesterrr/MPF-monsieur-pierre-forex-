# Getting an OANDA token

Ten minutes, no card, no deposit. This gets MPF real EURUSD and gold.

---

## 1. Open a practice account

1. Go to **oanda.com** and pick the site for your region. The regional sites are
   separate companies with separate account systems; a token from one will not
   work on another.
2. Register. Choose **Practice** / **Demo**, not Live.
3. Confirm your email and sign in.

A practice account gets the **same market data** as a live one. The difference
is that orders are simulated. For MPF, which only reads candles, there is no
downside at all.

## 2. Generate the token

1. Sign in to the OANDA web platform.
2. Open **My Account → Manage API Access** (sometimes under Account → API, the
   wording moves around).
3. Click **Generate** / **Revoke and Regenerate**.
4. Copy the token. It is a long hex string with a dash in the middle.

> It is shown once. Regenerating it invalidates the old one, which is also how
> you kill a token that has leaked.

## 3. Note which environment it belongs to

A practice token only works against the practice host, and a live token only
against the live host. MPF defaults to practice.

| | Host | `OANDA_ENV` |
|---|---|---|
| Practice | `api-fxpractice.oanda.com` | `practice` (default) |
| Live | `api-fxtrade.oanda.com` | `live` |

## 4. Give it to MPF

**Locally** — in the terminal you run MPF from:

```bash
export OANDA_TOKEN=your-token-here
python3 -m src.wsgi
```

Make it permanent by adding that `export` line to `~/.zshrc` (macOS) or
`~/.bashrc` (Linux). On Windows PowerShell:

```powershell
$env:OANDA_TOKEN = "your-token-here"
```

**Deployed** — set it as an environment variable in the host's dashboard, never
in a file. See `docs/DEPLOY.md`.

## 5. Check it worked

```bash
curl -s http://127.0.0.1:8000/api/health
```

`"oanda": true` means the key is loaded. Then pick EURUSD in the dashboard and
press load.

---

## Security — worth thirty seconds

**An OANDA token is account access, not just a data key.** The same token class
that reads candles can place and close orders through the trading endpoints.
MPF never calls those, but anyone holding your token could.

So:

- **Never commit it.** `.gitignore` covers `.env`; environment variables are the
  intended route.
- **Never put it in frontend code.** It lives on the server. The browser never
  sees it — that is why MPF has a backend at all.
- **Keep practice and live apart.** Use a practice token for MPF. There is no
  reason to point a live token at an analysis tool.
- **If it leaks, regenerate immediately.** That invalidates the old one.

---

## Troubleshooting

| What you see | What it means |
|---|---|
| `OANDA_TOKEN is not set` | The variable is not in the environment of the process running MPF. Exporting it in a different terminal tab does not count. |
| `HTTP 401` | Wrong token, or a practice token pointed at the live host. Check `OANDA_ENV`. |
| `HTTP 403` | The token is valid but the instrument is not in your account's list. |
| Gold missing | Metals are CFDs and availability varies by region and regulator. Check your account's instrument list; if XAU_USD is absent, Twelve Data also carries it. |
| `HTTP 429` | Too many requests. MPF caches each analysis for 60s, so this usually means something else is using the same token. |

## Limits

- **5000 candles per request.** MPF caps at that; it does not page. 5000 5-minute
  bars is about 17 days, which is enough for the weekly bias to resolve.
- Rate limits apply per token. Check OANDA's current developer docs for the
  numbers, as they change.

## Instruments MPF maps

Forex: EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURGBP EURJPY GBPJPY
Metals: XAUUSD XAGUSD
Index CFDs: NAS100 SPX500 US30

**The index CFDs are not the CME futures contracts.** Different product,
different hours, different settlement. Fine for reading structure, wrong if you
need the actual NQ or ES contract — see the README on why real futures data
costs money.
