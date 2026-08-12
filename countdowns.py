"""
Days until a configured, recurring annual date -- a birthday, a holiday.

Pure date arithmetic, no network, nothing that can fail or need a key,
the same keyless philosophy as weather_source.py and moon_phase.py.
"""

from datetime import date
from typing import Dict, List, Optional


def days_until(today: date, month: int, day: int) -> int:
    """Days from today to the next occurrence of month/day, this year or
    next. 0 means today."""
    candidate = _safe_date(today.year, month, day)
    if candidate < today:
        candidate = _safe_date(today.year + 1, month, day)
    return (candidate - today).days


def _safe_date(year: int, month: int, day: int) -> date:
    # Feb 29 in a year that is not a leap year has no real date to land
    # on -- the 28th is the closest one that actually exists.
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, min(day, 28))


def upcoming(events: Optional[List[Dict]], today: date,
            limit: int = 5) -> List[Dict]:
    """Configured events, each given a "days" field for how soon the next
    occurrence is, soonest first.

    A malformed entry (missing a name, an out-of-range month/day) is
    skipped rather than raised -- one bad row in the config should not
    take down every other one.
    """
    out = []
    for event in events or []:
        name = str(event.get("name") or "").strip()
        if not name:
            continue
        try:
            month, day = int(event.get("month")), int(event.get("day"))
            days = days_until(today, month, day)
        except (TypeError, ValueError):
            continue
        out.append({"name": name, "days": days})
    out.sort(key=lambda e: e["days"])
    return out[:limit]
