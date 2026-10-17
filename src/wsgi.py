"""MPF — Monsieur Pierre's Forex. One WSGI app, runs anywhere.

Local:   python3 -m src.wsgi            (stdlib wsgiref, no deps)
Vercel:  app.py exposes `app`
Render:  gunicorn app:app

Serves the dashboard and the API from one process, so there is nothing to
configure differently per host.

SECURITY — read this before deploying
-------------------------------------
Your OANDA token sits in this process's environment. Anyone who can reach a
deployed instance can make it call OANDA with *your* token. On a practice
account that burns your quota. On a live account the same token class can
place and close trades.

So: if MPF_TOKEN is set, every /api/ request must carry it. If MPF_TOKEN is
NOT set, the app refuses to serve the API to anything other than localhost.
There is deliberately no way to run this publicly without a token.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import time
import traceback
import urllib.parse
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

from .backtest.engine import Fills
from .backtest.replay import evaluate
from .data.feed import synthetic
from .data.providers import ProviderError, catalogue, get_bars, get_history
from .pipeline import run

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
#: Served alongside web/ because the CSS references ../assets/ for Pierre.
#: Each entry maps a URL prefix to a directory; both are escape-checked.
EXTRA_DIRS = {"assets": ROOT / "assets"}

_cache: dict = {}
CACHE_TTL = 60

TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

#: Rulebook §8 — which symbol to read divergence against.
SMT_DEFAULTS = {
    "EURUSD": "GBPUSD", "GBPUSD": "EURUSD",
    "XAUUSD": "XAGUSD", "NQ": "ES", "ES": "NQ",
}
#: These move against the base pair, so divergence is the mirror case.
INVERTED = {"DXY", "USDX"}


# ----------------------------------------------------------------- helpers

def _anchor_hour_utc() -> int:
    """17:00 New York in UTC — 21 under EDT, 22 under EST."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return 21 if datetime.now(ZoneInfo("America/New_York")).dst() else 22


def _is_local(environ: dict) -> bool:
    host = (environ.get("HTTP_HOST") or "").split(":")[0]
    addr = environ.get("REMOTE_ADDR") or ""
    # A forwarded header means a proxy in front of us, i.e. not local.
    if environ.get("HTTP_X_FORWARDED_FOR"):
        return False
    return host in ("localhost", "127.0.0.1", "::1") or addr in ("127.0.0.1", "::1")


def _authorised(environ: dict, q: dict) -> Tuple[bool, str]:
    """Returns (ok, reason)."""
    expected = os.environ.get("MPF_TOKEN")

    if not expected:
        if _is_local(environ):
            return True, ""
        return False, (
            "MPF_TOKEN is not set, so this instance only answers on localhost. "
            "Set MPF_TOKEN in the host's environment variables and reload.")

    supplied = (environ.get("HTTP_X_MPF_TOKEN")
                or q.get("k")
                or "")
    # Constant-time compare: a plain == leaks the token a character at a time.
    if supplied and hmac.compare_digest(supplied, expected):
        return True, ""
    return False, "Bad or missing key."


# ----------------------------------------------------------------- routes

def _api(path: str, q: dict) -> Tuple[int, dict]:
    if path == "/api/catalogue":
        # Reachability is computed in providers.catalogue so the picker can
        # grey out instruments whose provider has no key. Listing them as
        # available made a missing key look like a broken selector.
        return 200, {"instruments": catalogue(), "timeframes": TIMEFRAMES}

    if path == "/api/health":
        return 200, {"providers": {
            "oanda": bool(os.environ.get("OANDA_TOKEN")),
            "twelvedata": bool(os.environ.get("TWELVEDATA_KEY")),
            "yahoo": True,
        }, "protected": bool(os.environ.get("MPF_TOKEN"))}

    if path == "/api/analyze":
        if q.get("synthetic") == "1":
            payload = run(synthetic(n=12000), anchor_hour_utc=_anchor_hour_utc())
            payload["meta"].update(synthetic=True, symbol="SYNTHETIC",
                                   provider="none", note=None)
            return 200, payload

        symbol = (q.get("symbol") or "EURUSD").upper()
        tf = q.get("tf") or "5m"
        provider = q.get("provider") or None
        count = min(int(q.get("count") or 5000), 5000)

        key = (symbol, tf, provider, count)
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return 200, hit[1]

        # Interactive requests get a tight budget. A long one means the
        # dashboard sits on "…" with no explanation, which reads as frozen.
        bars = get_bars(symbol, tf, count=count, provider=provider,
                        budget_seconds=12.0)

        # SMT reference (Rulebook §8). A failure here degrades the SMT check
        # to "unmet" rather than failing the whole analysis.
        comparison = None
        ref = (q.get("smt") or SMT_DEFAULTS.get(symbol) or "").upper()
        if ref and ref != symbol:
            try:
                comparison = {"symbol": ref,
                              "bars": get_bars(ref, tf, count=count),
                              "inverted": ref in INVERTED}
            except ProviderError as e:
                print(f"  SMT reference {ref} unavailable: {e}")

        payload = run(bars, anchor_hour_utc=_anchor_hour_utc(), symbol=symbol,
                      comparison=comparison)
        payload["meta"].update(
            symbol=symbol, tf=tf, synthetic=False, provider=provider or "auto",
            smt_reference=(comparison or {}).get("symbol"),
            note=None if tf in ("1m", "5m", "15m") else
            f"Fetched at {tf}. Weekly and daily bias are resampled from this, "
            f"so the stack may be incomplete.",
        )
        _cache[key] = (time.time(), payload)
        return 200, payload

    if path == "/api/backtest":
        symbol = (q.get("symbol") or "EURUSD").upper()
        tf = q.get("tf") or "5m"
        count = min(int(q.get("count") or 5000), 5000)
        min_score = int(q.get("min_score") or 0)

        # A backtest needs months, not the 17 days one 5m request returns,
        # so this pages backwards. See providers.get_history.
        pages = min(int(q.get("pages") or 6), 12)
        bars = (synthetic(n=40000) if q.get("synthetic") == "1"
                else get_history(symbol, tf, pages=pages))

        fills = Fills(
            spread_pips=float(q.get("spread") or 1.0),
            slippage_pips=float(q.get("slippage") or 0.2),
            symbol=symbol,
        )
        out = evaluate(bars, anchor_hour_utc=_anchor_hour_utc(),
                       fills=fills, min_score=min_score)
        out["symbol"] = "SYNTHETIC" if q.get("synthetic") == "1" else symbol
        out["synthetic"] = q.get("synthetic") == "1"
        out["bars"] = len(bars)
        out["pages"] = pages
        return 200, out

    return 404, {"error": f"no route {path}"}


def _static(path: str) -> Tuple[int, bytes, str]:
    rel = "index.html" if path in ("/", "") else path.lstrip("/")

    # Pick the base directory from the first path segment.
    head = rel.split("/", 1)[0]
    if head in EXTRA_DIRS:
        base = EXTRA_DIRS[head].resolve()
        target = (base / rel.split("/", 1)[1]).resolve() if "/" in rel else base
    else:
        base = WEB.resolve()
        target = (base / rel).resolve()

    # Path traversal guard. ../../etc/passwd is the oldest trick there is,
    # and it has to be checked against whichever base we just chose.
    if not str(target).startswith(str(base)) or not target.is_file():
        return 404, b"not found", "text/plain"

    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return 200, target.read_bytes(), ctype


# ----------------------------------------------------------------- app

def app(environ: dict, start_response: Callable) -> Iterable[bytes]:
    path = environ.get("PATH_INFO", "/")
    q = {k: v[0] for k, v in
         urllib.parse.parse_qs(environ.get("QUERY_STRING", "")).items()}

    def send(status: int, body: bytes, ctype: str,
             extra: List[Tuple[str, str]] | None = None):
        reason = {200: "OK", 401: "Unauthorized", 404: "Not Found",
                  500: "Internal Server Error", 502: "Bad Gateway"}.get(status, "OK")
        headers = [("Content-Type", ctype), ("Content-Length", str(len(body)))]
        headers += extra or []
        # The dashboard is the only client; nothing here is embeddable.
        headers += [("X-Content-Type-Options", "nosniff"),
                    ("Referrer-Policy", "no-referrer")]
        start_response(f"{status} {reason}", headers)
        return [body]

    def sendjson(status: int, payload: dict):
        return send(status, json.dumps(payload, separators=(",", ":")).encode(),
                    "application/json", [("Cache-Control", "no-store")])

    if not path.startswith("/api/"):
        status, body, ctype = _static(path)
        cache = "no-cache" if ctype == "text/html" else "public, max-age=3600"
        return send(status, body, ctype, [("Cache-Control", cache)])

    ok, why = _authorised(environ, q)
    if not ok:
        return sendjson(401, {"error": why, "kind": "auth"})

    try:
        status, payload = _api(path, q)
        return sendjson(status, payload)
    except ProviderError as e:
        return sendjson(502, {"error": str(e), "kind": "provider"})
    except Exception as e:
        traceback.print_exc()
        return sendjson(500, {"error": f"{type(e).__name__}: {e}", "kind": "server"})


# ----------------------------------------------------------------- local run

def main() -> None:
    import argparse
    from wsgiref.simple_server import make_server

    p = argparse.ArgumentParser(description="MPF — Monsieur Pierre's Forex")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()

    have = [n for n, k in (("oanda", "OANDA_TOKEN"),
                           ("twelvedata", "TWELVEDATA_KEY"))
            if os.environ.get(k)]

    print(f"  MPF  http://{args.host}:{args.port}")
    print(f"  providers: {', '.join(have) or 'none'} (+ yahoo, keyless)")
    print(f"  api auth: {'MPF_TOKEN set' if os.environ.get('MPF_TOKEN') else 'localhost only'}")
    if not have:
        print("  no keys — add ?synthetic=1, or see docs/OANDA.md")

    make_server(args.host, args.port, app).serve_forever()


if __name__ == "__main__":
    main()
