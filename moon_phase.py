"""
Moon phase, computed from the calendar date alone.

No network, no API key, nothing that can fail or go stale: the moon's
phase is fully determined by elapsed time since a known reference new
moon, so this is exact arithmetic rather than a fetch.
"""

import math
from datetime import datetime, timezone

SYNODIC_MONTH = 29.530588853  # days from one new moon to the next

# A well-documented reference new moon (2000-01-06 18:14 UTC) -- any real
# new moon works equally well as the zero point, since only elapsed time
# modulo the synodic month matters.
_REFERENCE_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)

# Upper bound of each phase's slice of the 0..1 cycle (0/1 = new moon,
# 0.5 = full), each named phase given roughly its own eighth, with New
# and Full narrowed slightly since those are the two moments a name
# should actually mean "at or very near," not "closest of eight options."
_PHASE_BOUNDS = [
    (0.033, "New Moon"),
    (0.219, "Waxing Crescent"),
    (0.281, "First Quarter"),
    (0.469, "Waxing Gibbous"),
    (0.531, "Full Moon"),
    (0.719, "Waning Gibbous"),
    (0.781, "Last Quarter"),
    (0.967, "Waning Crescent"),
    (1.001, "New Moon"),
]


def phase_info(when: datetime) -> dict:
    """The moon's phase for a given date.

    Returns {"name", "fraction", "illumination", "waxing"}: fraction is
    0..1 through the cycle, illumination is 0..100 (percent of the disc
    lit), waxing is True while the lit fraction is still growing.

    Aware datetimes are converted to UTC before comparing to the UTC
    reference -- stripping tzinfo alone (local wall time treated as UTC)
    skewed the phase near New/Full on non-UTC clocks.
    """
    if when.tzinfo is not None:
        instant = when.astimezone(timezone.utc)
    else:
        # Naive inputs are treated as UTC, matching the reference epoch.
        instant = when.replace(tzinfo=timezone.utc)
    days = (instant - _REFERENCE_NEW_MOON).total_seconds() / 86400.0
    fraction = (days % SYNODIC_MONTH) / SYNODIC_MONTH
    illumination = round((1 - math.cos(2 * math.pi * fraction)) * 50)
    name = next(label for upper, label in _PHASE_BOUNDS if fraction < upper)
    return {
        "name": name,
        "fraction": fraction,
        "illumination": illumination,
        "waxing": fraction < 0.5,
    }
