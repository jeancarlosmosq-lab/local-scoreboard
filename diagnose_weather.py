#!/usr/bin/env python3
"""
Find out why the hourly and five-day forecast are not appearing, while the
current temperature is.

The plugin's weather fetch is a three-step chain:

    1. /points/{lat},{lon}       -> forecast office + grid square, and the
                                     URLs for the daily and hourly forecasts
    2. the daily forecast URL    -> today's and tonight's conditions, plus
                                     the periods the five-day list is built
                                     from
    3. the hourly forecast URL   -> the next several hours, one entry each

Current temperature comes from a separate, fourth call to the nearest
observation station, which is why it can work while the other three do not:
a failure anywhere in steps 1-3 leaves "hourly" and "daily" empty without
otherwise breaking the segment.

This script walks all three steps for your configured point and reports
exactly what came back at each one, so the fix targets the real point of
failure instead of guessing.

Run on the Pi:
    python3 diagnose_weather.py [latitude] [longitude]

Defaults to Bayonne, NJ if no coordinates are given. Needs nothing installed.
Paste the whole output back.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://api.weather.gov"


def get(url, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "LEDMatrix-diagnostic/1.0 (+github.com)",
                      "Accept": "application/geo+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode()), None, resp.status
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
        return None, f"HTTP {e.code}: {body}", e.code
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", None


def main():
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 40.6687
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else -74.1143

    print(f"Weather diagnostic  |  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"Point: {lat:.4f}, {lon:.4f}")
    print("=" * 72)

    print("\n[1] /points lookup -- resolves the grid and forecast URLs")
    data, err, status = get(f"{BASE}/points/{lat:.4f},{lon:.4f}")
    if err:
        print(f"    FAILED -- {err}")
        print("    Nothing downstream can work if this fails. Likely causes:")
        print("    network access blocked, or a malformed coordinate.")
        return 1

    props = (data or {}).get("properties") or {}
    forecast_url = props.get("forecast")
    hourly_url = props.get("forecastHourly")
    city = (props.get("relativeLocation") or {}).get(
        "properties", {}).get("city", "")
    print(f"    OK -- resolved near {city or '(city name not returned)'}")
    print(f"    forecast (daily) URL : {forecast_url or 'MISSING'}")
    print(f"    forecastHourly URL   : {hourly_url or 'MISSING'}")
    if not forecast_url:
        print("    'forecast' key is missing from the response -- this is")
        print("    the field the daily/five-day list depends on entirely.")
    if not hourly_url:
        print("    'forecastHourly' key is missing -- this is the field the")
        print("    hourly list depends on entirely.")

    print("\n[2] Daily forecast (also supplies the five-day list)")
    if forecast_url:
        fdata, ferr, fstatus = get(forecast_url)
        if ferr:
            print(f"    FAILED -- {ferr}")
        else:
            periods = ((fdata or {}).get("properties") or {}).get("periods") or []
            print(f"    OK -- {len(periods)} period(s) returned")
            daytime = [p for p in periods if p.get("isDaytime")]
            print(f"    {len(daytime)} of those are daytime periods "
                  f"(what the five-day list is built from)")
            for p in periods[:4]:
                print(f"      {p.get('name', '?'):16} "
                      f"daytime={p.get('isDaytime')}  "
                      f"temp={p.get('temperature')}  "
                      f"{p.get('shortForecast', '')}")
            if not daytime:
                print("    No daytime periods at all -- the five-day list")
                print("    will be empty even though this call succeeded.")
    else:
        print("    SKIPPED -- no forecast URL from step 1")

    print("\n[3] Hourly forecast")
    if hourly_url:
        hdata, herr, hstatus = get(hourly_url)
        if herr:
            print(f"    FAILED -- {herr}")
        else:
            hperiods = ((hdata or {}).get("properties") or {}).get("periods") or []
            print(f"    OK -- {len(hperiods)} period(s) returned")
            for p in hperiods[:4]:
                print(f"      {p.get('startTime', '?')[:16]}  "
                      f"temp={p.get('temperature')}  "
                      f"{p.get('shortForecast', '')}")
    else:
        print("    SKIPPED -- no forecastHourly URL from step 1")

    print("\n" + "=" * 72)
    print("""
What this means:

  If [2] or [3] shows a FAILED request, that is the fix target directly --
  the error message says why (blocked network, bad response, etc).

  If [2] succeeded but found zero daytime periods, or [3] succeeded but
  returned zero total periods, the plugin's parsing is not the problem --
  the data genuinely is not there for this point at this moment, which
  would be unusual but is worth ruling out.

  If everything above reports OK with periods present, the fetch itself is
  fine and the problem is in how the plugin stores or passes the result --
  a different kind of bug than a network or parsing one.

Paste this whole output back.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
