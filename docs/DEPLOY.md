# Deploying MPF

MPF is one WSGI app (`src/wsgi.py`, exposed as `app` in `app.py`). It runs the
same way locally, on Vercel, and on Render. Nothing is host-specific except the
config file.

---

## Before anything: set MPF_TOKEN

**A deployed MPF without `MPF_TOKEN` is an open door to your OANDA account.**

Anyone who finds the URL can make it call OANDA with your token. On a practice
account that burns your quota; the same token class can also place trades.

MPF refuses to serve its API publicly without a token — if `MPF_TOKEN` is unset
it answers only on localhost. That is deliberate and should not be worked around.

Generate one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it as an environment variable on the host. The browser asks for it once and
remembers it.

> This is one shared key, not user accounts. It is the right size of lock for a
> tool one person uses. If MPF ever gets more than one user, it needs real auth.

---

## Vercel (recommended)

Serverless, no idle spin-down, and a push deploys. The free Hobby tier is
**non-commercial only** — fine for personal use, not for anything you charge for.

1. Push the repo to GitHub.
2. **vercel.com → Add New → Project →** import the repo.
3. Framework preset: **Other**. No build command.
4. **Environment Variables**, before the first deploy:

   | Name | Value |
   |---|---|
   | `OANDA_TOKEN` | your practice token |
   | `OANDA_ENV` | `practice` |
   | `MPF_TOKEN` | the key you generated |
   | `TWELVEDATA_KEY` | optional |

5. Deploy.

`vercel.json` and `.python-version` are already in the repo. Vercel finds `app`
in `app.py` by itself.

**What it costs you:** nothing, within 1M invocations and 100 GB transfer a
month. One person clicking a dashboard will not come close.

**The catch:** each request is a fresh function instance, so the 60-second
analysis cache mostly will not hit. Each load is a real OANDA call — worth
knowing if you are on a tight free-tier allowance.

---

## Render (if you want a normal always-on process)

Free web service, 512 MB RAM. **Spins down after 15 idle minutes and takes
about a minute to wake**, so the first click after a break is slow.

1. Push to GitHub.
2. **render.com → New → Web Service →** connect the repo.
3. Build command: `pip install gunicorn`
4. Start command: `gunicorn app:app`
5. Add the same environment variables.

The in-process cache actually works here, since it is one long-lived process.

---

## Cloudflare Workers — don't

Workers caps CPU at **10 ms per invocation** on the free plan. The MPF pipeline
takes ~230 ms. Python support there is also limited. Cloudflare Pages is fine
for the static files, but the backend has to live elsewhere, and splitting them
is more setup than either option above.

---

## Rate limiting — your actual question

> *"I'm using it just for myself so I don't need rate limiting, right?"*

Half right.

**True:** you alone cannot exceed OANDA's limits by clicking a dashboard. MPF
caches each analysis for 60s, and a human cannot click fast enough to matter.

**Not true once deployed:** a public URL is not "just you". Bots scan IP ranges
and new domains constantly; they will find it. Without `MPF_TOKEN`, every one of
those hits becomes an OANDA call on your token.

So the thing protecting you is **the token, not rate limiting**. With it set,
unauthorised requests are rejected before any provider is called, and there is
nothing left to rate limit. That is why MPF has a token and no rate limiter.

---

## Keys: where they go and where they never go

```
OANDA_TOKEN ──► host environment variables ──► the server process
                                                      │
                                              calls OANDA from the server
                                                      │
                                              sends only candle data ──► browser
```

The browser never receives your OANDA token. That is the whole reason MPF has a
backend instead of calling OANDA from JavaScript — in a frontend-only app, every
visitor can read the key out of the network tab.

`MPF_TOKEN` is the only secret the browser holds, and it grants nothing beyond
this app.

**Never commit either.** `.gitignore` covers `.env`.

---

## After deploying, check

```bash
curl https://your-app.vercel.app/api/health
# {"error":"Bad or missing key.","kind":"auth"}   <-- correct

curl -H "X-MPF-Token: your-key" https://your-app.vercel.app/api/health
# {"providers":{"oanda":true,...},"protected":true}
```

If the first call returns data instead of a 401, `MPF_TOKEN` did not take.
Fix that before leaving it up.
