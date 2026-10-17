"""Run every detector over one dataset and emit the dashboard's JSON.

    python3 -m src.pipeline --synthetic --out web/data.json
    python3 -m src.pipeline --csv data/eurusd_5m.csv --out web/data.json
"""

from __future__ import annotations

import argparse
import json
from typing import List, Optional

from .core.bias import daily_bias, h1_profile, premium_discount, weekly_bias
from .core.confluence import LevelPool, build_report, check_smt, mark_plp
from .core.sessions import build_asian_ranges, classify_break
from .core.types import Bar, Direction, Level, LevelType, Timeframe
from .data.feed import TF_SECONDS, load_csv, resample, synthetic
from .detectors.crt import find_crts
from .detectors.levels import (
    find_fvgs, find_order_block_candidates, update_level_status,
)
from .detectors.model1 import find_model1
from .detectors.swings import find_swings, mark_sweeps


def _swing_levels(bars: List[Bar], tf: Timeframe) -> List[Level]:
    out = []
    for s in mark_sweeps(bars, find_swings(bars)):
        if s.mitigated_index is not None:
            continue
        out.append(Level(
            type=LevelType.SWING,
            direction=Direction.BEARISH if s.is_high else Direction.BULLISH,
            top=s.price, bottom=s.price,
            created_ts=s.ts, created_index=s.index, timeframe=tf,
        ))
    return out


def _level_json(l: Level) -> dict:
    return {
        "id": l.id, "type": l.type.value, "direction": l.direction.value,
        "top": round(l.top, 5), "bottom": round(l.bottom, 5),
        "ts": l.created_ts, "tf": l.timeframe.value,
        "status": l.status.value,
        "accepted": l.meta.get("accepted"),
        "meta": {k: v for k, v in l.meta.items() if k != "accepted"},
    }


def run(bars_m5: List[Bar], anchor_hour_utc: int = 21,
        comparison: Optional[dict] = None) -> dict:
    """Full top-down pass. Returns the dashboard payload.

    `comparison` carries the SMT reference series (Rulebook §8), shaped
    {"symbol": "GBPUSD", "bars": [...], "inverted": False}. DXY passes
    inverted=True because it moves against EURUSD. Omit it and the SMT check
    simply reports as unmet rather than silently passing.
    """
    tfs = {
        Timeframe.M5: bars_m5,
        Timeframe.M15: resample(bars_m5, Timeframe.M15),
        Timeframe.H1: resample(bars_m5, Timeframe.H1),
        Timeframe.H4: resample(bars_m5, Timeframe.H4),
        Timeframe.D1: resample(bars_m5, Timeframe.D1, anchor_hour_utc),
        Timeframe.W1: resample(bars_m5, Timeframe.W1, anchor_hour_utc),
    }

    price = bars_m5[-1].close

    # ---- bias stack (Rulebook §3)
    wk = tfs[Timeframe.W1]
    dy = tfs[Timeframe.D1]
    wb = weekly_bias(wk[-2], wk[-1], len(wk) - 1) if len(wk) >= 2 else None
    db = daily_bias(dy[-2], dy[-1], len(dy) - 1) if len(dy) >= 2 else None

    h1 = tfs[Timeframe.H1]
    h1_sw = mark_sweeps(h1, find_swings(h1))
    hp = h1_profile(
        [s.price for s in h1_sw if s.is_high],
        [s.price for s in h1_sw if not s.is_high],
    )

    # The bias actually used downstream: weekly leads (Model 1 is top-down).
    bias = wb.direction if wb and wb.tradeable else (
        db.direction if db and db.tradeable else Direction.NONE)

    # ---- premium / discount (Rulebook §4)
    zone, eq = premium_discount(dy[-1].high, dy[-1].low, price)

    # ---- level pool (Rulebook §2)
    pool = LevelPool()
    for tf in (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1):
        bars = tfs[tf]
        if len(bars) < 25:
            continue
        pool.add(update_level_status(bars, find_fvgs(bars, tf)))
        pool.add(update_level_status(bars, find_order_block_candidates(bars, tf)))
        pool.add(_swing_levels(bars, tf))

    target = pool.pick_target(bias, price, eq)

    # ---- CRT (Rulebook §5) — most recent on D1, else H4
    crt = None
    for tf in (Timeframe.D1, Timeframe.H4):
        found = find_crts(tfs[tf], tf, bias=bias)
        if found:
            crt = found[-1]
            break

    # ---- PLP + Model #1 (Rulebook §6, §7)
    plp = model1 = aligned = None
    if crt is not None:
        plp_tf = crt.timeframe.plp_tier or Timeframe.H1
        plp_bars = tfs[plp_tf]
        plp_sw = mark_sweeps(plp_bars, find_swings(plp_bars))

        # Map the CRT's C3 open onto the lower tier.
        c3_ts = crt.c3_ts
        as_of = max(0, sum(1 for b in plp_bars if b.ts <= c3_ts) - 1)

        plp = mark_plp(plp_bars, plp_sw, bias, as_of, plp_tf)
        if plp:
            aligned = pool.near(plp.price, tolerance_pips=5.0, exclude_tf=plp_tf)
            if plp.is_taken:
                model1 = find_model1(plp_bars, bias, plp.taken_index, plp_tf)

    # ---- Asian range (Rulebook §9)
    ranges = build_asian_ranges(bars_m5)
    asia = brk = None
    if ranges:
        key = sorted(ranges)[-1]
        asia = ranges[key]
        after = next((i for i, b in enumerate(bars_m5) if b.ts > asia.end_ts), None)
        if after is not None:
            brk = classify_break(bars_m5, asia, after)

    # ---- SMT (Rulebook §8) — needs a second symbol
    smt = None
    if comparison and comparison.get("bars") and plp is not None:
        try:
            plp_tf2 = crt.timeframe.plp_tier or Timeframe.H1
            ref = resample(comparison["bars"], plp_tf2)
            base = tfs[plp_tf2]
            if len(ref) > 10 and len(base) > 10:
                smt = check_smt(
                    base, mark_sweeps(base, find_swings(base)),
                    ref, mark_sweeps(ref, find_swings(ref)),
                    bias, as_of_index=len(base) - 1,
                    comp_inverted=bool(comparison.get("inverted")),
                )
                if smt:
                    smt["symbol"] = comparison.get("symbol", "?")
        except Exception as e:
            # A broken comparison feed must not take down the whole analysis.
            smt = None
            print(f"  SMT skipped: {type(e).__name__}: {e}")

    # ---- report (Rulebook §10)
    report = build_report(
        direction=bias,
        weekly_ok=bool(wb and wb.tradeable and wb.direction is bias),
        daily_ok=bool(db and db.tradeable and db.direction is bias),
        h1_ok=bool(hp.tradeable and hp.direction is bias),
        correct_side=bool(target and (
            (zone == "premium" and target.top < eq) or
            (zone == "discount" and target.bottom > eq))),
        target=target, crt=crt, plp=plp, plp_alignment=aligned, model1=model1,
        smt=smt,
    )

    return {
        "meta": {
            "pair": "EURUSD",
            "bars": len(bars_m5),
            "from": bars_m5[0].ts,
            "to": bars_m5[-1].ts,
            "price": round(price, 5),
        },
        "bias": {
            "weekly": {"direction": wb.direction.value, "rule": wb.rule} if wb else None,
            "daily": {"direction": db.direction.value, "rule": db.rule} if db else None,
            "h1": {"direction": hp.direction.value, "rule": hp.rule},
            "resolved": bias.value,
        },
        "zone": {"name": zone, "eq": round(eq, 5),
                 "day_high": round(dy[-1].high, 5), "day_low": round(dy[-1].low, 5)},
        "crt": {
            "tf": crt.timeframe.value, "direction": crt.direction.value,
            "range_high": round(crt.range_high, 5), "range_low": round(crt.range_low, 5),
            "eq": round(crt.eq, 5), "opposing": round(crt.opposing, 5),
            "c3_ts": crt.c3_ts,
        } if crt else None,
        "plp": {
            "price": round(plp.price, 5), "tf": plp.timeframe.value,
            "taken": plp.is_taken, "ts": plp.swing_ts,
            "aligned_with": [_level_json(l) for l in (aligned or [])],
        } if plp else None,
        "model1": {
            "entry": round(model1.entry, 5), "stop": round(model1.stop, 5),
            "risk_pips": round(model1.risk / 0.0001, 1),
            "direction": model1.direction.value, "ts": model1.candle_ts,
        } if model1 else None,
        "asia": {
            "date": asia.date_ny, "high": round(asia.high, 5),
            "low": round(asia.low, 5), "eq": round(asia.eq, 5),
            "size_pips": round(asia.size_pips, 1),
            "break": brk,
            "plp_on_edge": asia.near(plp.price) if (asia and plp) else None,
        } if asia else None,
        "levels": [_level_json(l) for l in pool.valid()],
        "candles": [
            {"t": b.ts, "o": b.open, "h": b.high, "l": b.low, "c": b.close}
            for b in tfs[Timeframe.H1][-200:]
        ],
        "smt": smt,
        "report": report.to_dict(),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="CRT confluence pass over EURUSD bars")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="5m OHLC CSV: time,open,high,low,close[,volume]")
    src.add_argument("--synthetic", action="store_true",
                     help="generate fake bars for UI work (NOT market data)")
    p.add_argument("--out", default="web/data.json")
    p.add_argument("--bars", type=int, default=2400)
    p.add_argument("--anchor-hour-utc", type=int, default=21,
                   help="daily open in UTC (17:00 NY = 21 EDT / 22 EST)")
    args = p.parse_args()

    bars = synthetic(n=args.bars) if args.synthetic else load_csv(args.csv)
    payload = run(bars, anchor_hour_utc=args.anchor_hour_utc)
    payload["meta"]["synthetic"] = bool(args.synthetic)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    r = payload["report"]
    print(f"wrote {args.out}")
    print(f"  bars      {payload['meta']['bars']}  "
          f"{'SYNTHETIC - not market data' if args.synthetic else ''}")
    print(f"  bias      {payload['bias']['resolved']}")
    print(f"  levels    {len(payload['levels'])}")
    print(f"  score     {r['score']}/{r['max_score']}")
    print(f"  missing   {', '.join(r['missing']) or 'nothing'}")


if __name__ == "__main__":
    main()
