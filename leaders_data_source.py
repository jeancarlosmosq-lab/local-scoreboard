"""
MLB StatsAPI data source for season statistical leaders.

Why MLB StatsAPI instead of ESPN: the ESPN leaders feed is inconsistent about
qualified-player filtering (an ERA leaderboard full of relievers with 4 innings
pitched is not useful) and its category names shift between sports. MLB's
StatsAPI exposes a single /stats/leaders endpoint with an explicit
playerPool=qualified filter and stable category names.

This module does exactly one thing: turn an HTTP response into a list of
LeaderRow dicts. It does no caching and no drawing -- see leaders_manager.py
and leaders_renderer.py for those.
"""

import logging
import unicodedata
from datetime import datetime
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Category keys are MLB StatsAPI's own names. The label is what gets drawn on
# the LED panel, so it is kept short -- a 128px panel has room for very little.
#
# "higher_is_better" drives both the sort order sanity check and the award
# scoring in awards_manager.py. ERA is the only inverted one here.
HITTING_CATEGORIES = {
    "battingAverage": {"label": "AVG", "higher_is_better": True},
    "hits": {"label": "HITS", "higher_is_better": True},
    "homeRuns": {"label": "HR", "higher_is_better": True},
    "runsBattedIn": {"label": "RBI", "higher_is_better": True},
    "runs": {"label": "RUNS", "higher_is_better": True},
    "stolenBases": {"label": "SB", "higher_is_better": True},
}

PITCHING_CATEGORIES = {
    "wins": {"label": "WINS", "higher_is_better": True},
    "earnedRunAverage": {"label": "ERA", "higher_is_better": False},
    "strikeouts": {"label": "K", "higher_is_better": True},
}

ALL_CATEGORIES = {**HITTING_CATEGORIES, **PITCHING_CATEGORIES}


# League scopes. MLB StatsAPI splits by leagueId; omitting it returns both
# leagues combined. MVP and Cy Young are per-league awards in reality, so the
# split is the more correct default for the awards screens.
LEAGUE_SCOPES = {
    "mlb": {"league_id": None, "label": "MLB"},
    "al": {"league_id": 103, "label": "AL"},
    "nl": {"league_id": 104, "label": "NL"},
}


# Full season stat lines, fetched per player from the /people endpoint.
#
# These are separate from the leaderboard categories above. A leaderboard tells
# you a player's value in ONE category; this tells you their whole line. That
# matters because the player leading in hits also has a batting average and a
# home run total, and the leaders endpoint will never mention them.
#
# Keys are StatsAPI's own stat field names -- note "strikeOuts" here versus
# the leaderboard category "strikeouts". They genuinely differ.
HITTING_STAT_FIELDS = [
    ("avg", "AVG"),
    ("homeRuns", "HR"),
    ("rbi", "RBI"),
    ("hits", "H"),
    ("runs", "R"),
    ("stolenBases", "SB"),
    ("ops", "OPS"),
]

PITCHING_STAT_FIELDS = [
    ("era", "ERA"),
    ("wins", "W"),
    ("losses", "L"),
    ("strikeOuts", "K"),
    ("whip", "WHIP"),
    ("inningsPitched", "IP"),
]

STAT_FIELD_LABELS = dict(HITTING_STAT_FIELDS + PITCHING_STAT_FIELDS)


# StatsAPI player pools. "QUALIFIED" applies MLB's plate-appearance and
# innings thresholds; "ROOKIES" restricts to rookie-eligible players.
#
# Rookie leaderboards are thinner than the qualified ones by nature -- a
# rookie rarely clears a full-season qualification bar -- so the rookie pool
# is fetched unqualified, and the limit does the filtering instead.
POOL_QUALIFIED = "qualified"
POOL_ROOKIES = "ROOKIES"


def ascii_fold(text: str) -> str:
    """Strip accents so bitmap fonts can render the name.

    LED matrix fonts are 5x7 or 4x6 bitmaps with no glyph beyond basic ASCII,
    so "Sanchez" spelled with an a-acute renders as a garbage character mid
    word -- which reads as a typo, not as a missing glyph. Baseball rosters are
    full of these: Sanchez, Ramirez, Rodriguez, Alvarez, Acuna, Pena.

    NFKD decomposition splits an accented letter into its base plus a
    combining mark, and dropping the marks leaves the base. Characters that do
    not decompose (the Scandinavian slashed o, for instance) are dropped
    outright rather than left to render as noise.
    """
    if not text:
        return text
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.encode("ascii", "ignore").decode("ascii")


def current_season() -> int:
    """Return the season year to query.

    Baseball seasons run inside a single calendar year, but January and
    February have no current-season stats yet -- asking for them returns an
    empty leaderboard. During those months we keep showing the season that just
    finished, which is what a viewer would expect from a scoreboard in winter.
    """
    now = datetime.now()
    return now.year - 1 if now.month < 3 else now.year


class MLBStatsLeadersSource:
    """Fetches season leaders from MLB StatsAPI."""

    BASE_URL = "https://statsapi.mlb.com/api/v1"

    # sportId 1 is MLB. Minor league IDs exist (11=AAA, 12=AA, ...) but their
    # leader feeds are sparse, so this plugin ships MLB-only.
    SPORT_ID_MLB = 1

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.session = requests.Session()

        # Same retry posture as the scoreboard plugin's data sources: back off
        # on rate limits and 5xx rather than dropping the whole leaderboard.
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # team.id -> abbreviation (e.g. 117 -> "HOU"). A leader entry's own
        # "team" object carries only an id and the full "Houston Astros"
        # name -- confirmed against a live request, where no "abbreviation"
        # or "teamName" field this file previously read for it was actually
        # present. /teams carries the abbreviation keyed by the same id, and
        # team ids are effectively permanent, so this is fetched once and
        # kept for the life of the source rather than looked up every call.
        self._team_abbrs: Optional[Dict[int, str]] = None
        self._team_ids: Optional[Dict[str, int]] = None

    def get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)",
            "Accept": "application/json",
        }

    def _load_teams(self) -> None:
        if self._team_abbrs is not None:
            return
        self._team_abbrs = {}
        self._team_ids = {}
        try:
            response = self.session.get(
                f"{self.BASE_URL}/teams",
                params={"sportId": self.SPORT_ID_MLB},
                headers=self.get_headers(), timeout=15,
            )
            response.raise_for_status()
            for team in response.json().get("teams", []) or []:
                tid, abbr = team.get("id"), team.get("abbreviation")
                if tid and abbr:
                    self._team_abbrs[tid] = abbr
                    self._team_ids[abbr] = tid
        except Exception as e:
            self.logger.debug("Could not fetch team abbreviations: %s", e)

    def _team_abbr(self, team_id) -> str:
        """Resolve a StatsAPI team id to its abbreviation, e.g. 117 -> "HOU"."""
        if not team_id:
            return ""
        self._load_teams()
        return self._team_abbrs.get(team_id, "")

    def _team_id(self, abbr: str) -> Optional[int]:
        """Resolve a team abbreviation to its StatsAPI id, e.g. "NYY" -> 147."""
        if not abbr:
            return None
        self._load_teams()
        return self._team_ids.get(abbr.upper())

    def fetch_team_roster(self, team_abbr: str) -> List[Dict]:
        """One team's active roster: [{"player_id", "name", "short_name"}].

        For a team MVP ranked against its own teammates rather than the
        whole league -- team_best() (awards_manager.py) only ever sees a
        player who already ranks in a league-wide leaderboard's top N,
        which leaves most followed teams with no MVP at all most of the
        time. This is the whole roster, so every team has one.
        """
        team_id = self._team_id(team_abbr)
        if not team_id:
            return []
        try:
            response = self.session.get(
                f"{self.BASE_URL}/teams/{team_id}/roster",
                params={"rosterType": "active"},
                headers=self.get_headers(), timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.debug("Could not fetch roster for %s: %s", team_abbr, e)
            return []

        out = []
        for entry in data.get("roster", []) or []:
            person = entry.get("person") or {}
            pid = str(person.get("id") or "")
            name = ascii_fold(person.get("fullName") or "")
            if not pid or not name:
                continue
            out.append({
                "player_id": pid,
                "name": name,
                "short_name": self._abbreviate_name(name),
            })
        return out

    def fetch_leaders(
        self,
        categories: List[str],
        stat_group: str,
        limit: int = 5,
        season: Optional[int] = None,
        scope: str = "mlb",
        player_pool: str = POOL_QUALIFIED,
    ) -> Dict[str, List[Dict]]:
        """Fetch leaders for several categories in one request.

        StatsAPI accepts a comma-separated leaderCategories list and returns
        one block per category, so five hitting categories cost one HTTP call
        rather than five. That matters on a Raspberry Pi that is also
        rendering frames.

        Args:
            categories: StatsAPI category keys, e.g. ["homeRuns", "hits"]
            stat_group: "hitting" or "pitching"
            limit: how many players per category
            season: season year, defaults to current_season()
            scope: "mlb" (both leagues), "al", or "nl"
            player_pool: POOL_QUALIFIED or POOL_ROOKIES

        Returns:
            {category_key: [ {rank, name, team, value, player_id}, ... ]}
            Missing or failed categories are simply absent from the dict.
        """
        if not categories:
            return {}

        season = season or current_season()
        params = {
            "leaderCategories": ",".join(categories),
            "statGroup": stat_group,
            "season": season,
            "sportId": self.SPORT_ID_MLB,
            "limit": limit,
            # Without this, ERA and other rate stats are topped by pitchers
            # with a handful of innings. "qualified" applies MLB's own
            # plate-appearance / innings-pitched thresholds.
            "playerPool": player_pool,
        }

        league_id = LEAGUE_SCOPES.get(scope, {}).get("league_id")
        if league_id:
            params["leagueId"] = league_id

        try:
            response = self.session.get(
                f"{self.BASE_URL}/stats/leaders",
                params=params,
                headers=self.get_headers(),
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.error(
                f"Error fetching {scope}/{stat_group}/{player_pool} leaders "
                f"from MLB StatsAPI: {e}"
            )
            return {}

        return self._parse_leaders(data)

    def _parse_leaders(self, data: Dict) -> Dict[str, List[Dict]]:
        """Flatten the StatsAPI response into {category: [rows]}.

        The response shape is:
            {"leagueLeaders": [
                {"leaderCategory": "homeRuns",
                 "leaders": [{"rank": 1, "value": "34",
                              "person": {"id": 1, "fullName": "..."},
                              "team": {"id": 117, "name": "Houston Astros"}}]}]}

        team carries an id and the full team name, not an abbreviation --
        confirmed against a live request; _team_abbr() resolves the id.
        Every field is defensively read -- StatsAPI omits `team` for free
        agents and occasionally omits `rank` early in a season.
        """
        results: Dict[str, List[Dict]] = {}

        for block in data.get("leagueLeaders", []) or []:
            category = block.get("leaderCategory")
            if not category:
                continue

            rows: List[Dict] = []
            for i, entry in enumerate(block.get("leaders", []) or [], start=1):
                person = entry.get("person") or {}
                team = entry.get("team") or {}

                name = ascii_fold(person.get("fullName") or "")
                if not name:
                    continue

                rows.append(
                    {
                        "rank": int(entry.get("rank") or i),
                        "name": name,
                        "short_name": self._abbreviate_name(name),
                        "team": ascii_fold(self._team_abbr(team.get("id"))),
                        "value": str(entry.get("value") or ""),
                        "player_id": str(person.get("id") or ""),
                    }
                )

            if rows:
                results[category] = rows

        return results

    def fetch_player_stats(
        self,
        player_ids: List[str],
        stat_group: str,
        season: Optional[int] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Fetch full season stat lines for a batch of players.

        The leaders endpoint only returns each player's value in the category
        they ranked in. To show a hits leader's batting average, or an ERA
        leader's strikeout total, the actual stat line has to be fetched
        separately -- that is what this does.

        StatsAPI's /people endpoint accepts a comma-separated personIds list
        and a hydrate expression, so ~30 players cost one HTTP request rather
        than thirty.

        Returns {player_id: {"AVG": ".312", "HR": "41", ...}}. Players whose
        stats are unavailable are simply absent.
        """
        if not player_ids:
            return {}

        season = season or current_season()
        fields = (
            HITTING_STAT_FIELDS if stat_group == "hitting" else PITCHING_STAT_FIELDS
        )

        # URLs have length limits and StatsAPI gets unhappy with very long id
        # lists, so batch rather than sending one enormous request.
        results: Dict[str, Dict[str, str]] = {}
        batch_size = 40

        for start in range(0, len(player_ids), batch_size):
            batch = [pid for pid in player_ids[start : start + batch_size] if pid]
            if not batch:
                continue

            params = {
                "personIds": ",".join(batch),
                "hydrate": (
                    f"stats(group=[{stat_group}],type=[season],season={season})"
                ),
            }

            try:
                response = self.session.get(
                    f"{self.BASE_URL}/people",
                    params=params,
                    headers=self.get_headers(),
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                self.logger.error(f"Error fetching {stat_group} player stats: {e}")
                continue

            results.update(self._parse_player_stats(data, fields))

        self.logger.debug(
            f"Fetched {stat_group} stat lines for "
            f"{len(results)}/{len(player_ids)} players"
        )
        return results

    def fetch_rookie_ids(
        self, player_ids: List[str], season: Optional[int] = None
    ) -> set:
        """Return which of these players debuted in the given season.

        This is the fallback path for Rookie of the Year. The primary path is
        StatsAPI's ROOKIES player pool; if that returns nothing (the value is
        undocumented and may change), this filters the ordinary leaderboards
        by debut date instead.

        It is an approximation, and deliberately a conservative one: MLB's
        actual rookie rule allows a player who debuted in a *previous* season
        to retain eligibility if they stayed under 130 at-bats, 50 innings
        pitched, and 45 active-roster days. Those counts are not on this
        endpoint, so a September call-up from last year who qualifies in
        reality will be missed here. Everyone this returns is genuinely a
        rookie; not every rookie is returned.
        """
        if not player_ids:
            return set()

        season = season or current_season()
        rookies = set()
        batch_size = 40

        for start in range(0, len(player_ids), batch_size):
            batch = [pid for pid in player_ids[start : start + batch_size] if pid]
            if not batch:
                continue

            try:
                response = self.session.get(
                    f"{self.BASE_URL}/people",
                    params={"personIds": ",".join(batch)},
                    headers=self.get_headers(),
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                self.logger.debug(f"Rookie debut lookup failed: {e}")
                continue

            for person in data.get("people", []) or []:
                debut = person.get("mlbDebutDate") or ""
                pid = str(person.get("id") or "")
                if pid and debut[:4].isdigit() and int(debut[:4]) == season:
                    rookies.add(pid)

        self.logger.debug(
            f"Debut-date rookie filter matched {len(rookies)}/{len(player_ids)}"
        )
        return rookies

    @staticmethod
    def _parse_player_stats(data: Dict, fields) -> Dict[str, Dict[str, str]]:
        """Flatten /people?hydrate=stats into {player_id: {LABEL: value}}.

        The nesting is people -> stats -> splits -> stat, and any level can be
        missing for a player with no recorded appearances, so every step is
        guarded rather than indexed directly.
        """
        out: Dict[str, Dict[str, str]] = {}

        for person in data.get("people", []) or []:
            pid = str(person.get("id") or "")
            if not pid:
                continue

            flat: Dict[str, str] = {}

            for block in person.get("stats") or []:
                splits = block.get("splits") or []
                if not splits:
                    continue
                stat = (splits[0] or {}).get("stat") or {}
                for key, label in fields:
                    value = stat.get(key)
                    if value is None or value == "":
                        continue
                    text = str(value)
                    # StatsAPI returns rate stats as "0.312"; baseball writes
                    # them ".312", and the leading zero is a wasted pixel
                    # column on a panel this small.
                    if text.startswith("0.") and label in ("AVG", "OPS", "OBP", "SLG"):
                        text = text[1:]
                    flat[label] = text

            if flat:
                out[pid] = flat

        return out


    # Generational suffixes are part of the name, not the surname. Taking the
    # last token blindly turns "Vladimir Guerrero Jr." into "V.Jr.", which is
    # useless on screen -- the suffix has to be skipped to find the surname.
    _NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

    @classmethod
    def _abbreviate_name(cls, full_name: str) -> str:
        """Turn "Shohei Ohtani" into "S.Ohtani".

        A 128x32 panel fits roughly 10-14 characters per row alongside a rank
        and a stat value, so full names do not fit. First initial plus surname
        is the standard baseball convention and stays recognisable.

        Suffixes are dropped rather than kept: "V.Guerrero" is clearer at this
        size than "V.Guerrero Jr.", and there is no active MLB player whose
        surname collides with a suffixed relative's.
        """
        parts = [p for p in full_name.split() if p]
        if len(parts) < 2:
            return full_name

        # Walk backwards past any suffix tokens to find the real surname.
        surname_idx = len(parts) - 1
        while surname_idx > 0 and parts[surname_idx].lower().strip(".") in {
            s.strip(".") for s in cls._NAME_SUFFIXES
        }:
            surname_idx -= 1

        if surname_idx == 0:
            return full_name

        return f"{parts[0][0]}.{parts[surname_idx]}"
