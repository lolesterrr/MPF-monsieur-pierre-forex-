"""Market hours, and cleaning what vendors send outside them.

Why this exists: a weekend-padded series produced a 66-pip "FVG" across the
Friday-to-Sunday gap in testing. The detector was right — there IS a gap — but
the gap is the weekend, not structure. Those levels are artifacts, and a
backtest built on them looks like it found something.

Forex runs from Sunday 17:00 New York to Friday 17:00 New York, DST-aware.
Everything here works in NY time for that reason and stores UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from .types import Bar

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

OPEN_HOUR_NY = 17    # Sunday 17:00 NY
CLOSE_HOUR_NY = 17   # Friday 17:00 NY

#: Instruments that do NOT follow the forex week. Futures and indices have
#: their own calendars, so cleaning is skipped rather than guessed at.
NON_FOREX = {"NQ", "ES", "YM", "RTY", "GOLD", "SILVER", "OIL",
             "NAS100", "SPX500", "US30", "DXY"}


#: Anchor: Sunday 4 January 1970, 17:00 New York, the first Sunday open.
#: Built once — constructing it per call made session_id so slow that a
#: single analysis hung, because the detectors call it per bar triple.
_ANCHOR = datetime(1970, 1, 4, OPEN_HOUR_NY, tzinfo=NY).timestamp()
_WEEK = 7 * 86400


def _ny(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(NY)


@lru_cache(maxsize=65536)
def _ny_parts(hour_bucket: int) -> tuple:
    """(weekday, hour, ny_timestamp) for an hour bucket.

    Cached at hour granularity: market hours and week boundaries never change
    within an hour, and this turns hundreds of thousands of timezone
    conversions into a few thousand.
    """
    d = _ny(hour_bucket * 3600)
    return d.weekday(), d.hour, d.timestamp()


def is_open(ts: int) -> bool:
    """Is the forex market open at this instant?"""
    wd, hour, _ = _ny_parts(ts // 3600)

    if wd == 5:                                   # Saturday: always shut
        return False
    if wd == 6:                                   # Sunday: opens at 17:00
        return hour >= OPEN_HOUR_NY
    if wd == 4:                                   # Friday: shuts at 17:00
        return hour < CLOSE_HOUR_NY
    return True                                   # Mon-Thu


def session_id(ts: int) -> int:
    """Which trading week a bar belongs to.

    Two bars with different session ids are separated by a weekend, so a gap
    between them is the market being shut, not a fair value gap.

    Keyed by the timestamp of that week's open.

    Note on the obvious-looking wrong version: dividing the raw epoch by seven
    days does NOT work. Epoch zero was a Thursday, so those buckets break on
    Thursdays and a Friday-to-Monday gap lands inside one bucket. The week has
    to be anchored to an actual Sunday 17:00 New York, which also keeps it
    correct across DST changes.
    """
    d = _ny(ts)
    # weekday(): Mon=0 .. Sun=6, so this counts days back to Sunday.
    days_since_sunday = (d.weekday() + 1) % 7
    open_dt = (d - timedelta(days=days_since_sunday)).replace(
        hour=OPEN_HOUR_NY, minute=0, second=0, microsecond=0)
    if d < open_dt:
        open_dt -= timedelta(days=7)
    return int(open_dt.timestamp())


@dataclass
class CleanReport:
    kept: int = 0
    removed_closed: int = 0
    removed_flat: int = 0
    removed_future: int = 0
    session_breaks: List[int] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""

    @property
    def removed(self) -> int:
        return self.removed_closed + self.removed_flat + self.removed_future

    def to_dict(self) -> dict:
        return {
            "kept": self.kept,
            "removed": self.removed,
            "removed_market_closed": self.removed_closed,
            "removed_flat_padding": self.removed_flat,
            "removed_future": self.removed_future,
            "session_breaks": len(self.session_breaks),
            "skipped": self.skipped,
            "reason": self.reason,
        }


def clean(bars: List[Bar], symbol: Optional[str] = None,
          now: Optional[int] = None) -> Tuple[List[Bar], CleanReport]:
    """Strip bars that should not exist.

    Three kinds get removed:

    1. **Outside market hours.** Vendors pad the weekend so the series looks
       continuous. Those bars are fiction.
    2. **Flat padding.** open == high == low == close with no volume, which is
       a vendor filling a hole rather than a real quote.
    3. **The future.** Timestamps after now, which mean a timezone bug
       somewhere upstream. Dropping them beats charting them.

    Futures and indices keep their own calendars, so cleaning is skipped for
    them rather than guessed at.
    """
    rep = CleanReport()
    if not bars:
        return bars, rep

    if symbol and symbol.upper() in NON_FOREX:
        rep.skipped = True
        rep.kept = len(bars)
        rep.reason = f"{symbol} is not on the forex calendar — not cleaned"
        return bars, rep

    now = now or int(datetime.now(tz=UTC).timestamp())
    # One bar of slack: the bar currently forming is legitimately ahead of now.
    horizon = now + 86400

    out: List[Bar] = []
    for b in bars:
        if b.ts > horizon:
            rep.removed_future += 1
            continue
        if not is_open(b.ts):
            rep.removed_closed += 1
            continue
        if b.high == b.low and b.open == b.close and not b.volume:
            rep.removed_flat += 1
            continue
        out.append(b)

    rep.kept = len(out)

    # Record where the weekends are, so detectors can refuse to look across them.
    for i in range(1, len(out)):
        if session_id(out[i].ts) != session_id(out[i - 1].ts):
            rep.session_breaks.append(i)

    return out, rep


def same_session(a: Bar, b: Bar) -> bool:
    """True when two bars are in the same trading week."""
    return session_id(a.ts) == session_id(b.ts)


def last_close_ts(now: Optional[int] = None) -> int:
    """When the market last shut, or 0 if it is open right now."""
    now = now or int(datetime.now(tz=UTC).timestamp())
    if is_open(now):
        return 0
    d = _ny(now)
    # Walk back to Friday 17:00 NY.
    days_back = (d.weekday() - 4) % 7
    friday = d.replace(hour=CLOSE_HOUR_NY, minute=0, second=0, microsecond=0)
    friday = friday.fromtimestamp(friday.timestamp() - days_back * 86400, tz=NY)
    return int(friday.timestamp())


def market_status(now: Optional[int] = None) -> dict:
    """What to show the user instead of an error when nothing is moving."""
    now = now or int(datetime.now(tz=UTC).timestamp())
    if is_open(now):
        return {"open": True, "label": "market open"}

    d = _ny(now)
    wd = d.weekday()
    if wd == 4 or (wd == 5) or (wd == 6 and d.hour < OPEN_HOUR_NY):
        when = "Sunday 17:00 New York"
    else:
        when = "shortly"
    return {
        "open": False,
        "label": "market closed",
        "detail": f"Forex is shut for the weekend. It reopens {when}. "
                  f"The last candle is Friday's close — that is correct, "
                  f"not a stale feed.",
    }
