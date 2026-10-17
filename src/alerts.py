"""Setup alerts.

    python3 -m src.alerts                 # scan once
    python3 -m src.alerts --watch 300     # scan every 5 minutes

Sends to Telegram if MPF_TELEGRAM_TOKEN and MPF_TELEGRAM_CHAT_ID are set;
otherwise prints. Nothing is configured by default, so this is silent until
you set it up — see .env.example.

An alert says "the conditions you wrote down are present", not "take this
trade". The score is a count, and §10's weights are untested.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .data.providers import ProviderError, get_bars
from .pipeline import run

STATE = Path(os.environ.get("MPF_STATE_DIR", ".")) / ".mpf-alerts.json"


def _watchlist() -> List[str]:
    raw = os.environ.get("MPF_WATCHLIST", "EURUSD")
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _min_score() -> int:
    try:
        return int(os.environ.get("MPF_ALERT_MIN_SCORE", "10"))
    except ValueError:
        return 10


def _load_state() -> Dict[str, str]:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(s: Dict[str, str]) -> None:
    try:
        STATE.write_text(json.dumps(s))
    except Exception as e:
        print(f"  (couldn't persist alert state: {e})")


def _fingerprint(payload: dict) -> str:
    """What makes this alert distinct from the last one.

    Keyed on the setup, not the clock — otherwise a --watch loop re-sends the
    same setup every five minutes until it expires.
    """
    m1 = payload.get("model1") or {}
    crt = payload.get("crt") or {}
    return "|".join(str(x) for x in (
        payload["bias"]["resolved"], crt.get("c3_ts"),
        m1.get("ts"), m1.get("entry"), payload["report"]["score"]))


def send(text: str) -> bool:
    token = os.environ.get("MPF_TELEGRAM_TOKEN")
    chat = os.environ.get("MPF_TELEGRAM_CHAT_ID")

    if not (token and chat):
        print(text)
        return False

    data = urllib.parse.urlencode({
        "chat_id": chat, "text": text, "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
            if not ok:
                print("  telegram rejected the message")
            return bool(ok)
    except Exception as e:
        # A dead notifier must not stop the scan.
        print(f"  telegram failed ({type(e).__name__}: {e}); printing instead")
        print(text)
        return False


def format_alert(symbol: str, payload: dict) -> str:
    r = payload["report"]
    m1 = payload.get("model1")
    crt = payload.get("crt")
    bias = payload["bias"]["resolved"]

    lines = [
        f"*MPF · {symbol}*",
        f"{bias} · {r['score']}/{r['max_score']} conditions",
        "",
    ]
    if crt:
        lines.append(f"CRT {crt['tf']} · T1 {crt['eq']:.5f} · T2 {crt['opposing']:.5f}")
    if m1:
        lines += [
            f"entry {m1['entry']:.5f}",
            f"stop  {m1['stop']:.5f}  ({m1['risk_pips']:.0f} pips)",
        ]
    if payload.get("plp"):
        n = len(payload["plp"].get("aligned_with") or [])
        lines.append(f"PLP {payload['plp']['price']:.5f} · {n} HTF levels aligned")

    missing = r.get("missing") or []
    if missing:
        lines += ["", "missing: " + ", ".join(missing[:4])]

    lines += ["", "_A condition count, not a signal. Weights untested._"]
    return "\n".join(lines)


def scan(symbols: Optional[List[str]] = None, tf: str = "5m",
         min_score: Optional[int] = None, force: bool = False) -> List[dict]:
    """One pass over the watchlist. Returns whatever it alerted on."""
    symbols = symbols or _watchlist()
    threshold = _min_score() if min_score is None else min_score
    state = _load_state()
    fired = []

    for sym in symbols:
        try:
            bars = get_bars(sym, tf, count=5000)
            payload = run(bars)
        except ProviderError as e:
            print(f"  {sym}: {e}")
            continue
        except Exception as e:
            print(f"  {sym}: {type(e).__name__}: {e}")
            continue

        score = payload["report"]["score"]
        fp = _fingerprint(payload)
        stamp = datetime.now(timezone.utc).strftime("%H:%M")

        if score < threshold:
            print(f"  {stamp} {sym}: {score}/{payload['report']['max_score']} — below {threshold}")
            continue

        if not force and state.get(sym) == fp:
            print(f"  {stamp} {sym}: {score} — already alerted on this setup")
            continue

        send(format_alert(sym, payload))
        state[sym] = fp
        fired.append({"symbol": sym, "score": score, "payload": payload})
        print(f"  {stamp} {sym}: ALERT at {score}")

    _save_state(state)
    return fired


def main() -> None:
    p = argparse.ArgumentParser(description="MPF setup alerts")
    p.add_argument("--symbols", help="comma separated; default MPF_WATCHLIST")
    p.add_argument("--tf", default="5m")
    p.add_argument("--min-score", type=int, default=None)
    p.add_argument("--watch", type=int, metavar="SECONDS",
                   help="loop forever, scanning every N seconds")
    p.add_argument("--force", action="store_true",
                   help="re-send even if this setup already alerted")
    args = p.parse_args()

    syms = ([s.strip().upper() for s in args.symbols.split(",")]
            if args.symbols else None)

    configured = bool(os.environ.get("MPF_TELEGRAM_TOKEN")
                      and os.environ.get("MPF_TELEGRAM_CHAT_ID"))
    print(f"  watching {syms or _watchlist()} at {args.tf}")
    print(f"  threshold {args.min_score if args.min_score is not None else _min_score()}"
          f"/15 · delivery: {'telegram' if configured else 'stdout only'}")

    if not args.watch:
        scan(syms, args.tf, args.min_score, args.force)
        return

    # A forex feed has nothing new at the weekend; the interval is yours to set.
    while True:
        try:
            scan(syms, args.tf, args.min_score, args.force)
        except KeyboardInterrupt:
            print("\n  stopped")
            return
        except Exception as e:
            print(f"  scan failed, continuing: {type(e).__name__}: {e}")
        time.sleep(max(60, args.watch))


if __name__ == "__main__":
    main()
