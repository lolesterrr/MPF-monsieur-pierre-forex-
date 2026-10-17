"""Check a deployed MPF instance.

    python3 -m src.checkdeploy https://your-app.vercel.app --key YOUR_MPF_TOKEN

Run this straight after deploying. It answers the three questions that matter
and that are easy to get silently wrong:

  1. Is the API locked? An open instance lets anyone spend your data quota.
  2. Did the provider keys actually load into the host's environment?
  3. Does a real analysis come back, and is the data fresh?

Checks nothing about your strategy. Only that the plumbing is right.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "


def _get(url: str, key: Optional[str] = None,
         timeout: int = 60) -> Tuple[int, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "mpf-checkdeploy"})
    if key:
        req.add_header("X-MPF-Token", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"error": body[:300]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def run(base: str, key: Optional[str], symbol: str = "EURUSD") -> int:
    base = base.rstrip("/")
    problems = 0

    print(f"\n  {base}\n")

    # 1. the page itself
    status, _ = _get(f"{base}/api/health")
    if status == 0:
        print(f"{BAD} unreachable. Is the deploy finished?")
        return 1

    # 2. is it locked?
    print("  AUTH")
    if status == 401:
        print(f"{OK} API rejects unauthenticated requests")
    elif status == 200:
        print(f"{BAD} API answered WITHOUT a key.")
        print("       Anyone who finds this URL can spend your data quota.")
        print("       Set MPF_TOKEN in the host's environment and redeploy.")
        problems += 1
    else:
        print(f"{WARN} unexpected status {status} with no key")

    if not key:
        print(f"{WARN} no --key given; skipping the authenticated checks")
        return problems

    status, health = _get(f"{base}/api/health", key)
    if status != 200:
        print(f"{BAD} the key was rejected: {health.get('error')}")
        print("       Check MPF_TOKEN on the host matches what you passed here.")
        return problems + 1
    print(f"{OK} key accepted")

    # 3. provider credentials
    print("\n  PROVIDERS")
    provs = health.get("providers", {})
    for name, live in provs.items():
        if name == "yahoo":
            print(f"{OK} yahoo (keyless — undocumented, see docs/DATA.md)")
        elif live:
            print(f"{OK} {name} key loaded")
        else:
            print(f"{WARN} {name} has no key on the host")

    if not any(v for k, v in provs.items() if k != "yahoo"):
        print(f"{WARN} no keyed provider configured. Live data will fall back")
        print("       to Yahoo or fail. Add TWELVEDATA_KEY on the host.")

    # 4. a real analysis
    print(f"\n  ANALYSIS ({symbol})")
    status, data = _get(f"{base}/api/analyze?symbol={symbol}&tf=5m", key, timeout=120)
    if status != 200:
        print(f"{BAD} {data.get('error', '')[:220]}")
        return problems + 1

    meta = data.get("meta", {})
    print(f"{OK} {meta.get('bars')} bars from {meta.get('provider')}")
    print(f"{OK} last price {meta.get('price')}")

    age = meta.get("age_label", "?")
    if meta.get("stale"):
        print(f"{WARN} data is {age} — not usable for a live read")
        print("       Dukascopy publishes per completed hour; for live use a")
        print("       keyed provider (Twelve Data) must be configured.")
    else:
        print(f"{OK} data is {age}")

    score = data.get("report", {}).get("score")
    print(f"{OK} analysis ran, {score}/{data['report']['max_score']} conditions")

    print("\n  " + ("All good." if not problems else
                    f"{problems} problem(s) above — fix before relying on it."))
    print("  Reminder: a condition count is not a signal.\n")
    return problems


def main() -> None:
    p = argparse.ArgumentParser(description="Check a deployed MPF instance")
    p.add_argument("url", help="e.g. https://mpf.vercel.app")
    p.add_argument("--key", help="MPF_TOKEN you set on the host")
    p.add_argument("--symbol", default="EURUSD")
    a = p.parse_args()
    sys.exit(1 if run(a.url, a.key, a.symbol) else 0)


if __name__ == "__main__":
    main()
