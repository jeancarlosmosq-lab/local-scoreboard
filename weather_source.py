"""
Weather from the US National Weather Service.

Free, keyless, and the authoritative source for US forecasts -- the same data
the commercial services resell. It asks for a User-Agent identifying the
caller, which is the only condition of use.

The lookup is two-stage: a point resolves to a forecast office and grid
square, and that grid is what actually serves forecasts. The first step never
changes for a fixed location, so it is resolved once and kept.

Three things are fetched, in descending order of how fast they change:

    alerts       warnings and watches -- the reason to look at all
    current      temperature and conditions now
    forecast     today's high and low, and tonight

Alerts matter most. A severe thunderstorm warning for Hudson County is worth
interrupting a scoreboard for; the temperature is not.
"""

import logging
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE = "https://api.weather.gov"

# Alert severities worth showing, most serious first. Anything below Minor is
# advisory noise on a display this size.
SEVERITY_ORDER = ["Extreme", "Severe", "Moderate", "Minor"]


class NWSWeather:
    """Current conditions, a short forecast and active alerts for one point."""

    def __init__(self, logger: logging.Logger, latitude: float, longitude: float,
                 label: str = "", units: str = "F"):
        self.logger = logger
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.label = label
        self.units = (units or "F").upper()

        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)

        self._grid: Optional[Dict] = None

    def _headers(self):
        # NWS asks callers to identify themselves; this is the only condition
        # of use, and requests without it are rejected.
        return {
            "User-Agent": "LEDMatrix/1.0 (github.com/ChuckBuilds/LEDMatrix)",
            "Accept": "application/geo+json",
        }

    def _get(self, url: str, params: Dict = None):
        try:
            response = self.session.get(url, params=params or {},
                                        headers=self._headers(), timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.debug("Weather request failed (%s): %s", url, e)
            return None

    # ------------------------------------------------------------------
    def _resolve_grid(self) -> Optional[Dict]:
        """Point -> forecast office and grid square. Fixed for a location."""
        if self._grid:
            return self._grid
        data = self._get(f"{BASE}/points/{self.latitude:.4f},{self.longitude:.4f}")
        properties = (data or {}).get("properties") or {}
        forecast_url = properties.get("forecast")
        if not forecast_url:
            return None
        self._grid = {
            "forecast": forecast_url,
            "hourly": properties.get("forecastHourly", ""),
            "stations": properties.get("observationStations", ""),
            "city": (properties.get("relativeLocation") or {})
                    .get("properties", {}).get("city", ""),
        }
        return self._grid

    @staticmethod
    def _to_display(celsius, units: str):
        """NWS reports Celsius; convert unless Celsius was asked for."""
        if celsius is None:
            return None
        try:
            value = float(celsius)
        except (TypeError, ValueError):
            return None
        if units == "C":
            return int(round(value))
        return int(round(value * 9.0 / 5.0 + 32.0))

    # ------------------------------------------------------------------
    def fetch(self) -> Dict:
        """Everything worth showing, in one dict. Empty on failure."""
        grid = self._resolve_grid()
        if not grid:
            return {}

        out = {
            "label": (self.label or grid.get("city") or "").upper(),
            "units": self.units,
            "alerts": self._fetch_alerts(),
        }

        forecast = self._get(grid["forecast"])
        periods = ((forecast or {}).get("properties") or {}).get("periods") or []
        if periods:
            now = periods[0]
            out["period"] = (now.get("name") or "").upper()
            out["condition"] = (now.get("shortForecast") or "").upper()
            out["temp"] = now.get("temperature")
            out["temp_unit"] = now.get("temperatureUnit", "F")
            # The next period is the other half of the day -- tonight's low
            # if it is currently daytime, tomorrow's high if it is not.
            if len(periods) > 1:
                nxt = periods[1]
                out["next_name"] = (nxt.get("name") or "").upper()
                out["next_temp"] = nxt.get("temperature")
                out["next_condition"] = (nxt.get("shortForecast") or "").upper()

        # Five daily periods, skipping the one already shown as "now".
        out["daily"] = self._condense_daily(periods)
        out["hourly"] = self._fetch_hourly(grid)

        current = self._fetch_current(grid)
        if current:
            out.update(current)

        return out

    @staticmethod
    def _condense_daily(periods, days: int = 5):
        """Daytime periods only, as a five-day outlook.

        NWS alternates day and night periods, so taking the first five
        outright would give two and a half days. Only the daytime ones carry
        the high, which is what a five-day forecast means to a reader.
        """
        out = []
        for period in periods:
            if not period.get("isDaytime"):
                continue
            out.append({
                "name": (period.get("name") or "")[:3].upper(),
                "temp": period.get("temperature"),
                "condition": (period.get("shortForecast") or ""),
            })
            if len(out) >= days:
                break
        return out

    def _fetch_hourly(self, grid: Dict, hours: int = 5):
        """The next few hours, one entry each."""
        if not grid.get("hourly"):
            return []
        data = self._get(grid["hourly"])
        periods = ((data or {}).get("properties") or {}).get("periods") or []
        out = []
        for period in periods[:hours]:
            when = period.get("startTime", "")
            label = ""
            if when:
                try:
                    from datetime import datetime
                    moment = datetime.fromisoformat(
                        when.replace("Z", "+00:00")).astimezone()
                    hour = moment.strftime("%I").lstrip("0") or "12"
                    label = f"{hour}{moment.strftime('%p')[0]}"
                except Exception:
                    label = ""
            out.append({
                "name": label,
                "temp": period.get("temperature"),
                "condition": (period.get("shortForecast") or ""),
            })
        return out

    def _fetch_current(self, grid: Dict) -> Dict:
        """Latest observation from the nearest reporting station."""
        stations = self._get(grid.get("stations", "")) if grid.get("stations") else None
        features = (stations or {}).get("features") or []
        if not features:
            return {}
        station_id = ((features[0].get("properties") or {}).get("stationIdentifier"))
        if not station_id:
            return {}

        observation = self._get(f"{BASE}/stations/{station_id}/observations/latest")
        properties = (observation or {}).get("properties") or {}
        temperature = (properties.get("temperature") or {}).get("value")
        display = self._to_display(temperature, self.units)
        if display is None:
            return {}

        # "Feels like" is heat index in summer and wind chill in winter; NWS
        # reports them as separate fields and only one is ever populated.
        feels_source = ((properties.get("heatIndex") or {}).get("value")
                        or (properties.get("windChill") or {}).get("value"))
        feels = self._to_display(feels_source, self.units)

        return {
            "now_temp": display,
            "now_feels": feels,
            "now_condition": (properties.get("textDescription") or "").upper(),
        }

    def _fetch_alerts(self) -> List[Dict]:
        """Active warnings and watches for this point, most serious first."""
        data = self._get(f"{BASE}/alerts/active",
                         {"point": f"{self.latitude:.4f},{self.longitude:.4f}"})
        alerts = []
        for feature in (data or {}).get("features", []) or []:
            properties = feature.get("properties") or {}
            event = (properties.get("event") or "").upper()
            if not event:
                continue
            alerts.append({
                "event": event,
                "severity": properties.get("severity", "Unknown"),
                "urgency": properties.get("urgency", ""),
            })

        def rank(alert):
            try:
                return SEVERITY_ORDER.index(alert["severity"])
            except ValueError:
                return len(SEVERITY_ORDER)

        alerts.sort(key=rank)
        return alerts
