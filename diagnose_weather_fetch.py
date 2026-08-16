#!/usr/bin/env python3
"""
Run the plugin's own weather_source.py against live data and print exactly
what it returns.

diagnose_weather.py already confirmed the raw NWS API has real hourly data
for this point. This script goes one step further: it imports the actual
NWSWeather class the plugin uses -- not a reimplementation of the request
logic -- and calls fetch() on it directly, the same way manager.py does.
If hourly comes back empty here, the bug is in this file's parsing. If it
comes back populated here but the panel still doesn't show it, the bug is
downstream in manager.py or strip_renderer.py instead.

Run from the plugin's own folder on the Pi, so the import can find it:

    cd ~/LEDMatrix/plugin-repos/local-scoreboard
    python3 diagnose_weather_fetch.py [latitude] [longitude]

Needs the plugin's own requests library, already installed for LEDMatrix.
Paste the whole output back.
"""

import json
import logging
import sys

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")


def main():
    try:
        from weather_source import NWSWeather
    except ImportError as e:
        print(f"Could not import weather_source.py: {e}")
        print("Run this from inside the local-scoreboard plugin folder:")
        print("  cd ~/LEDMatrix/plugin-repos/local-scoreboard")
        print("  python3 diagnose_weather_fetch.py")
        return 1

    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 40.6687
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else -74.1143

    print(f"Running the plugin's own NWSWeather.fetch() for {lat:.4f}, {lon:.4f}")
    print("=" * 72)

    logger = logging.getLogger("diagnose")
    weather = NWSWeather(logger, lat, lon, label="TEST", units="F")

    result = weather.fetch()

    print("\n" + "=" * 72)
    print("Raw returned dict (this is exactly what manager.py stores and")
    print("passes to the renderer):\n")
    print(json.dumps(result, indent=2, default=str))

    print("\n" + "=" * 72)
    print("Summary:")
    print(f"  label         : {result.get('label')!r}")
    print(f"  now_temp      : {result.get('now_temp')!r}")
    print(f"  now_condition : {result.get('now_condition')!r}")
    print(f"  hourly entries: {len(result.get('hourly') or [])}")

    if not result:
        print("\n  fetch() returned an EMPTY dict -- _resolve_grid() likely")
        print("  failed. Check for a 'Weather request failed' line above,")
        print("  logged at DEBUG level by the failing request.")
    elif not result.get("hourly"):
        print("\n  hourly is empty despite fetch() succeeding overall -- this")
        print("  points at _fetch_hourly() specifically, not the network")
        print("  layer. Check the DEBUG lines above for it logging a")
        print("  request failure.")

    print("\nPaste this whole output back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
