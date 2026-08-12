"""
Moon phase, computed from the calendar date alone.

No network, no API key, nothing that can fail or go stale: the moon's
phase is fully determined by elapsed time since a known reference new
moon, so this is exact arithmetic rather than a fetch.
"""

import math
from datetime import datetime

SYNODIC_MONTH = 29.530588853  # days from one new moon to the next

# A well-documented reference new moon (2000-01-06 18:14 UTC) -- any real
# new moon works equally well as the zero point, since only elapsed time
# modulo the synodic month matters.
_REFERENCE_NEW_MOON = datetime(2000, 1, 6, 18, 14)

# Upper bound of each phase's slice of the 0..1 cycle (0/1 = new moon,
# 0.5 = full), each named phase given roughly its own eighth, with New
# and Full narrowed slightly since those are the two moments a name
# should actually mean "at or very near," not "closest of eight options."
_PHASE_BOUNDS = [
    (0.033, "NEW MOON"),
    (0.219, "WAXING CRESCENT"),
    (0.281, "FIRST QUARTER"),
    (0.469, "WAXING GIBBOUS"),
    (0.531, "FULL MOON"),
    (0.719, "WANING GIBBOUS"),
    (0.781, "LAST QUARTER"),
    (0.967, "WANING CRESCENT"),
    (1.001, "NEW MOON"),
]


def phase_info(when: datetime) -> dict:
    """The moon's phase for a given date.

    Returns {"name", "fraction", "illumination", "waxing"}: fraction is
    0..1 through the cycle, illumination is 0..100 (percent of the disc
    lit), waxing is True while the lit fraction is still growing.
    """
    naive = when.replace(tzinfo=None) if when.tzinfo else when
    days = (naive - _REFERENCE_NEW_MOON).total_seconds() / 86400.0
    fraction = (days % SYNODIC_MONTH) / SYNODIC_MONTH
    illumination = round((1 - math.cos(2 * math.pi * fraction)) * 50)
    name = next(label for upper, label in _PHASE_BOUNDS if fraction < upper)
    return {
        "name": name,
        "fraction": fraction,
        "illumination": illumination,
        "waxing": fraction < 0.5,
    }
