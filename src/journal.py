"""Trade journal.

    python3 -m src.journal log --symbol EURUSD --direction long \\
        --entry 1.0845 --stop 1.0820 --t1 1.0880 --t2 1.0920 --note "asia sweep"
    python3 -m src.journal close --id 3 --exit 1.0880 --outcome t1_only
    python3 -m src.journal stats

Plain CSV, so you can open it in anything and it outlives this tool.

Why bother: the backtest tells you what the *rules* did. The journal tells you
what *you* did. Those diverge, and the gap is usually where the money goes.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .core.types import pip_size

JOURNAL = Path("journal.csv")

FIELDS = [
    "id", "opened_utc", "symbol", "direction", "model",
    "entry", "stop", "t1", "t2", "risk_pips",
    "score", "conditions_met", "conditions_missing",
    "closed_utc", "exit", "outcome", "r_multiple", "note",
]


def _read() -> List[Dict[str, str]]:
    if not JOURNAL.exists():
        return []
    with JOURNAL.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write(rows: List[Dict[str, str]]) -> None:
    with JOURNAL.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def log(**kw) -> dict:
    rows = _read()
    next_id = max((int(r["id"]) for r in rows if r.get("id", "").isdigit()),
                  default=0) + 1

    symbol = kw["symbol"].upper()
    entry, stop = float(kw["entry"]), float(kw["stop"])
    risk = abs(entry - stop)
    if risk <= 0:
        raise SystemExit("entry and stop cannot be the same price")
    pip = pip_size(symbol)   # gold is 0.01, JPY is 0.01, majors are 0.0001

    row = {
        "id": str(next_id),
        "opened_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": symbol,
        "direction": kw["direction"],
        "model": kw.get("model") or "crt",
        "entry": f"{entry:.5f}", "stop": f"{stop:.5f}",
        "t1": f"{float(kw['t1']):.5f}" if kw.get("t1") else "",
        "t2": f"{float(kw['t2']):.5f}" if kw.get("t2") else "",
        "risk_pips": f"{risk / pip:.1f}",
        "score": str(kw.get("score") or ""),
        "conditions_met": kw.get("met") or "",
        "conditions_missing": kw.get("missing") or "",
        "closed_utc": "", "exit": "", "outcome": "open", "r_multiple": "",
        "note": kw.get("note") or "",
    }
    rows.append(row)
    _write(rows)
    return row


def close(trade_id: int, exit_price: float, outcome: str,
          note: Optional[str] = None) -> dict:
    rows = _read()
    for r in rows:
        if r.get("id") == str(trade_id):
            if r.get("outcome") not in ("open", "", None):
                raise SystemExit(f"trade {trade_id} is already closed")
            entry, stop = float(r["entry"]), float(r["stop"])
            risk = abs(entry - stop)
            gain = (exit_price - entry) if r["direction"].startswith("l") \
                else (entry - exit_price)
            r["closed_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            r["exit"] = f"{exit_price:.5f}"
            r["outcome"] = outcome
            r["r_multiple"] = f"{gain / risk:.2f}"
            if note:
                r["note"] = (r.get("note", "") + " | " + note).strip(" |")
            _write(rows)
            return r
    raise SystemExit(f"no trade with id {trade_id}")


def stats() -> dict:
    rows = [r for r in _read() if r.get("r_multiple")]
    if not rows:
        return {"n": 0, "note": "No closed trades yet."}

    rs = [float(r["r_multiple"]) for r in rows]
    wins = [r for r in rs if r > 0]

    by_score: Dict[str, List[float]] = {}
    for r in rows:
        if r.get("score"):
            by_score.setdefault(r["score"], []).append(float(r["r_multiple"]))

    return {
        "n": len(rs),
        "win_rate": round(len(wins) / len(rs), 3),
        "expectancy_r": round(sum(rs) / len(rs), 3),
        "total_r": round(sum(rs), 2),
        "best": round(max(rs), 2),
        "worst": round(min(rs), 2),
        "by_score": {k: {"n": len(v), "expectancy_r": round(sum(v) / len(v), 3)}
                     for k, v in sorted(by_score.items())},
        "reliable": len(rs) >= 30,
        "note": ("Fewer than 30 trades — these numbers are noise."
                 if len(rs) < 30 else
                 "Your actual results. Compare against the backtest: a gap "
                 "between the two is execution, not strategy."),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="MPF trade journal")
    sub = p.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log", help="record a new trade")
    for a, req in (("--symbol", True), ("--direction", True),
                   ("--entry", True), ("--stop", True)):
        lg.add_argument(a, required=req)
    for a in ("--t1", "--t2", "--score", "--model", "--met", "--missing", "--note"):
        lg.add_argument(a)

    cl = sub.add_parser("close", help="close an open trade")
    cl.add_argument("--id", type=int, required=True)
    cl.add_argument("--exit", type=float, required=True)
    cl.add_argument("--outcome", default="closed",
                    choices=["t1_only", "t2", "stopped", "t1_then_stopped",
                             "manual", "closed"])
    cl.add_argument("--note")

    sub.add_parser("stats", help="summarise closed trades")
    ls = sub.add_parser("list", help="show trades")
    ls.add_argument("--open", action="store_true", help="only open ones")

    a = p.parse_args()

    if a.cmd == "log":
        r = log(symbol=a.symbol, direction=a.direction, entry=a.entry,
                stop=a.stop, t1=a.t1, t2=a.t2, score=a.score, model=a.model,
                met=a.met, missing=a.missing, note=a.note)
        print(f"  logged #{r['id']}  {r['symbol']} {r['direction']} "
              f"{r['entry']} / stop {r['stop']} ({r['risk_pips']} pips)")

    elif a.cmd == "close":
        r = close(a.id, a.exit, a.outcome, a.note)
        print(f"  closed #{r['id']}  {r['outcome']}  {r['r_multiple']}R")

    elif a.cmd == "stats":
        s = stats()
        if not s["n"]:
            print(f"  {s['note']}")
            return
        print(f"  {s['n']} closed · {s['win_rate']*100:.0f}% win · "
              f"{s['expectancy_r']:+.2f}R avg · {s['total_r']:+.2f}R total")
        print(f"  best {s['best']:+.2f}R · worst {s['worst']:+.2f}R")
        if s["by_score"]:
            print("  by confluence score:")
            for k, v in s["by_score"].items():
                print(f"    {k:>3}/15 · {v['n']:>3} trades · {v['expectancy_r']:+.2f}R")
        print(f"  {s['note']}")

    elif a.cmd == "list":
        rows = _read()
        if a.open:
            rows = [r for r in rows if r.get("outcome") == "open"]
        if not rows:
            print("  nothing to show")
            return
        for r in rows:
            tail = (f"{r['outcome']} {r['r_multiple']}R"
                    if r.get("r_multiple") else "open")
            print(f"  #{r['id']:>3} {r['symbol']:<8} {r['direction']:<6} "
                  f"{r['entry']} → {tail}")


if __name__ == "__main__":
    main()
