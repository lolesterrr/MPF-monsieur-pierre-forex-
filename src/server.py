"""Local web server for the dashboard.

    python3 -m src.server            # http://localhost:8000

Serves web/ and three endpoints:

    GET /api/catalogue              instruments this build can reach
    GET /api/analyze?symbol=&tf=    fetch bars, run the full pass, return JSON
    GET /api/health                 which providers have credentials

Stdlib only, single-threaded, bound to localhost. It holds your API keys in
memory, so it is a local tool, not something to expose to a network. If this
ever goes on a host, it needs auth, HTTPS and rate limiting first — none of
which are here.
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Tuple

from .data.feed import resample, synthetic
from .data.providers import BY_NAME, ProviderError, catalogue, get_bars
from .pipeline import run

WEB = Path(__file__).resolve().parents[1] / "web"

#: analyses are cached briefly so clicking between timeframes doesn't burn
#: through a free-tier request allowance
_cache: dict = {}
CACHE_TTL = 60  # seconds


def _anchor_hour_utc() -> int:
    """17:00 New York in UTC — 21 during EDT, 22 during EST."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ny = datetime.now(ZoneInfo("America/New_York"))
    return 21 if ny.dst() else 22


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            print(f"  {self.path}")

    # -- helpers ---------------------------------------------------

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> Tuple[str, dict]:
        u = urllib.parse.urlparse(self.path)
        return u.path, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    # -- routes ----------------------------------------------------

    def do_GET(self):  # noqa: N802
        path, q = self._query()

        if not path.startswith("/api/"):
            return super().do_GET()

        try:
            if path == "/api/catalogue":
                return self._json({
                    "instruments": catalogue(),
                    "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                })

            if path == "/api/health":
                return self._json({"providers": {
                    "oanda": bool(os.environ.get("OANDA_TOKEN")),
                    "twelvedata": bool(os.environ.get("TWELVEDATA_KEY")),
                    "yahoo": True,   # no key, but undocumented and fragile
                }})

            if path == "/api/analyze":
                return self._analyze(q)

            return self._json({"error": f"no route {path}"}, 404)

        except ProviderError as e:
            # The vendor's own words. Guessing here wastes the user's time.
            return self._json({"error": str(e), "kind": "provider"}, 502)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}",
                               "kind": "server"}, 500)

    def _analyze(self, q: dict) -> None:
        symbol = (q.get("symbol") or "EURUSD").upper()
        tf = q.get("tf") or "5m"
        provider = q.get("provider") or None
        count = min(int(q.get("count") or 5000), 5000)

        if q.get("synthetic") == "1":
            bars = synthetic(n=12000)
            payload = run(bars, anchor_hour_utc=_anchor_hour_utc())
            payload["meta"].update(synthetic=True, symbol="SYNTHETIC",
                                   tf=tf, provider="none")
            return self._json(payload)

        import time
        key = (symbol, tf, provider, count)
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return self._json(hit[1])

        bars = get_bars(symbol, tf, count=count, provider=provider)

        # The pipeline resamples upward from its input, so it needs the lowest
        # timeframe available. Anything above 5m can't build a daily or weekly
        # bias — say so rather than returning a confidently empty stack.
        payload = run(bars, anchor_hour_utc=_anchor_hour_utc())
        payload["meta"].update(
            symbol=symbol, tf=tf, synthetic=False,
            provider=provider or "auto",
            note=None if tf in ("1m", "5m", "15m")
            else f"Fetched at {tf}; weekly and daily bias need enough history "
                 f"to resample, so the stack may be incomplete.",
        )

        _cache[key] = (time.time(), payload)
        return self._json(payload)


def main() -> None:
    p = argparse.ArgumentParser(description="CRT confluence dashboard server")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1",
                   help="localhost only by default — this holds your API keys")
    args = p.parse_args()

    have = [n for n, k in (("oanda", "OANDA_TOKEN"),
                           ("twelvedata", "TWELVEDATA_KEY"))
            if os.environ.get(k)]

    print(f"  serving {WEB}")
    print(f"  http://{args.host}:{args.port}")
    print(f"  providers with keys: {', '.join(have) or 'none'} (+ yahoo, keyless)")
    if not have:
        print("  no keys set — only Yahoo instruments will load, "
              "or add ?synthetic=1")

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
