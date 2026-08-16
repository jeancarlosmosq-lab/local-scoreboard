"""
ESPN data source for the Local Scoreboard board.

One endpoint shape serves all three leagues:

    site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard
    site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={id}

which is why this plugin can show a Yankees game, a Knicks game and a Giants
game on the same board without three separate integrations. The scoreboard
call gives status, scores and records; the summary call gives the game
leaders -- the notable players and their stat lines.

No API key, no rate limit worth worrying about at this volume.
"""

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# league key -> (ESPN sport path, ESPN league path)
LEAGUES = {
    "mlb": ("baseball", "mlb"),
    "nba": ("basketball", "nba"),
    "nfl": ("football", "nfl"),
    # Spain's top flight only, not every competition a club plays --
    # ESPN's soccer endpoints are organised one competition at a time
    # (league, cup, continental) rather than one feed per club the way
    # the other three sports are, and this plugin's team model already
    # assumes one league per followed team. A club that also plays a cup
    # or Champions League needs a second team entry against that
    # competition's own league key once one is added here.
    "laliga": ("soccer", "esp.1"),
}

# The default roster of teams. rivals is deliberately the well-known
# classic matchups, not a guess -- unlike a birthday, a rivalry is public
# knowledge, so a sensible default costs nothing here.
DEFAULT_TEAMS = [
    {"abbr": "NYY", "league": "mlb", "name": "Yankees", "rivals": ["BOS", "NYM"]},
    {"abbr": "NYM", "league": "mlb", "name": "Mets", "rivals": ["ATL", "PHI", "NYY"]},
    {"abbr": "BKN", "league": "nba", "name": "Nets", "rivals": ["NYK"]},
    {"abbr": "NYK", "league": "nba", "name": "Knicks", "rivals": ["BOS", "BKN"]},
    {"abbr": "NYG", "league": "nfl", "name": "Giants", "rivals": ["DAL", "PHI"]},
]

# Abbreviations that are spelled more than one way in the wild. A team is
# matched if the game's abbreviation appears anywhere in the same group, so
# it does not matter which spelling the feed uses or which one you configure.
#
# This exists because getting it wrong is silent: an abbreviation that never
# matches yields an empty board rather than an error, which looks identical
# to a team simply not playing.
ABBR_ALIASES = [
    {"NYK", "NY"},        # Knicks
    {"GS", "GSW"},        # Warriors
    {"SA", "SAS"},        # Spurs
    {"NO", "NOP"},        # Pelicans
    {"UTAH", "UTA"},      # Jazz
    {"WSH", "WAS"},       # Wizards / Commanders / Nationals
    {"PHX", "PHO"},       # Suns
    {"LAR", "LA"},        # Rams
    {"JAX", "JAC"},       # Jaguars
    {"SF", "SFO"},        # 49ers / Giants
    {"TB", "TBR", "TAM"}, # Rays / Buccaneers
    {"KC", "KCR", "KAN"}, # Royals / Chiefs
    {"SD", "SDG"},        # Padres
    {"AZ", "ARI"},        # Diamondbacks
    {"CWS", "CHW"},       # White Sox
    {"ATH", "OAK"},       # Athletics
]


def abbr_group(abbr: str) -> set:
    """Every spelling equivalent to this abbreviation, including itself."""
    key = (abbr or "").strip().upper()
    if not key:
        return set()
    for group in ABBR_ALIASES:
        if key in group:
            return set(group)
    return {key}

# Game states, normalised across leagues. ESPN reports these per sport with
# slightly different vocabulary, so they are collapsed here.
STATE_LIVE = "live"
STATE_FINAL = "final"
STATE_UPCOMING = "upcoming"


# ESPN's category codes are internal shorthand, and some of them say nothing
# to a viewer -- "RAT" is a composite rating, not a statistic anyone follows.
# Anything not translated here is replaced by a label derived from the stat
# line itself, so the board never shows a code it cannot explain.
CATEGORY_LABELS = {
    "RAT": "",            # composite rating: opaque, drop it
    "MLBRATING": "",
    "RATING": "",
    "HR": "HR",
    "HOMERUNS": "HR",
    "RBI": "RBI",
    "RBIS": "RBI",
    "AVG": "AVG",
    "H": "HITS",
    "HITS": "HITS",
    "SB": "SB",
    "W": "WINS",
    "WINS": "WINS",
    "ERA": "ERA",
    "K": "K",
    "SO": "K",
    "STRIKEOUTS": "K",
    "SV": "SAVE",
    "PTS": "PTS",
    "POINTS": "PTS",
    "REB": "REB",
    "AST": "AST",
    "PASSINGYARDS": "PASS",
    "PYDS": "PASS",
    "RUSHINGYARDS": "RUSH",
    "RYDS": "RUSH",
    "RECEIVINGYARDS": "REC",
    "RECYDS": "REC",
    "GOAL": "GOAL",
    "GOALS": "GOAL",
    "TOTALSHOTS": "SHOTS",
    "SHOTS": "SHOTS",
}

# Which side of the game a performance belongs to, so a board can show one
# of each rather than two pitchers.
SIDE_PITCHING = "pitching"
SIDE_BATTING = "batting"


def classify_leader(league: str, category: str, line: str) -> str:
    """Whether a performance is a pitching or a batting line.

    ESPN does not label this directly, so it is read off the stat line: an
    innings figure or an earned-run average is a pitcher; an at-bat line like
    "2-4" or a home run total is a hitter.
    """
    text = f"{category} {line}".upper()
    if league != "mlb":
        return SIDE_BATTING
    pitching_marks = (" IP", "ERA", "SV", "K,", " K ", "W-", "QS")
    if any(mark in text for mark in pitching_marks):
        return SIDE_PITCHING
    if "-" in line and any(ch.isdigit() for ch in line):
        return SIDE_BATTING
    if any(mark in text for mark in ("HR", "RBI", "AVG", "SB")):
        return SIDE_BATTING
    return SIDE_BATTING


def readable_category(league: str, category: str, line: str) -> str:
    """A label a viewer can act on, never an internal code."""
    key = (category or "").upper().replace(" ", "")
    if key in CATEGORY_LABELS:
        mapped = CATEGORY_LABELS[key]
        if mapped:
            return mapped
    elif key and key.isalpha() and len(key) <= 4:
        # An unrecognised short code that at least looks like a statistic.
        return key

    side = classify_leader(league, category, line)
    if league == "mlb":
        return "PITCH" if side == SIDE_PITCHING else "BAT"
    if league == "nfl":
        upper = line.upper()
        # A completions-attempts pair -- "24-31" -- marks a quarterback; a
        # carry or catch line has no such pair.
        if re.search(r"\b\d+-\d+\b", upper) and "YDS" in upper:
            return "PASS"
        if "REC" in upper:
            return "REC"
        if "YDS" in upper:
            return "RUSH"
        return "TOP"
    return "TOP"


def ascii_fold(text: str) -> str:
    """Strip accents so bitmap fonts can render the name.

    Same reasoning as the leaderboard plugin: an LED matrix font carries no
    glyph beyond basic ASCII, so an accented letter renders as a stray mark
    mid-word and reads as a misspelling rather than a missing glyph.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.encode("ascii", "ignore").decode("ascii")


def abbreviate_name(full_name: str) -> str:
    """"Aaron Judge" -> "A.Judge", with suffixes skipped.

    Only the first letter of the initial and of the last name are
    capitalised -- the initial is a single letter so it's capital either
    way, but the last name itself used to come back fully upper (A.JUDGE)
    to stay legible at seven pixels beside a score. Matches the title
    case used everywhere else on the strip now.
    """
    folded = ascii_fold(full_name)
    parts = [p for p in folded.split() if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0].capitalize()

    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    idx = len(parts) - 1
    while idx > 0 and parts[idx].lower().strip(".") in suffixes:
        idx -= 1
    if idx == 0:
        return parts[0].capitalize()
    return f"{parts[0][0].upper()}.{parts[idx].capitalize()}"


class ESPNGamesSource:
    """Fetches games and game leaders for a set of teams."""

    BASE = "https://site.api.espn.com/apis/site/v2/sports"

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.session = requests.Session()
        retry = Retry(
            total=4, backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self):
        return {
            "User-Agent": "LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    def fetch_scoreboard(self, league: str, days_back: int = 3,
                         days_forward: int = 7) -> Optional[List[Dict]]:
        """Fetch a league's games across a date window.

        A window rather than a single day: "recent" needs a few days of
        finals (notable performers and headline ranking still consult
        them) and "upcoming" needs the next week, and one request covers
        both. Default history is 3 days -- confirmed in 0.21.0 notes;
        a 1-day window aged finals out before their own recap was useful.

        Returns an empty list when the request succeeded but there are no
        games in the window. Returns None when the request itself failed --
        callers must not treat that the same as "no games", or a single
        league outage wipes that league's already-known scores off the board.
        """
        if league not in LEAGUES:
            return []
        sport, league_path = LEAGUES[league]

        start = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
        end = (datetime.now() + timedelta(days=days_forward)).strftime("%Y%m%d")

        try:
            response = self.session.get(
                f"{self.BASE}/{sport}/{league_path}/scoreboard",
                params={"dates": f"{start}-{end}", "limit": 200},
                headers=self._headers(), timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.error("Error fetching %s scoreboard: %s", league, e)
            return None

        return self._parse_events(data, league)

    # A different host and path than everything else here (site.web.api,
    # not site.api) -- confirmed live, this is simply where ESPN serves
    # standings from, not a typo.
    STANDINGS_BASE = "https://site.web.api.espn.com/apis/v2/sports"

    def fetch_standings(self, league: str) -> Dict[str, str]:
        """Each team's current streak ("W3", "L2"), keyed by abbreviation.

        One request for the whole league rather than one per team --
        standings are already grouped by division/conference, and every
        team's streak comes back in that same response.
        """
        if league not in LEAGUES:
            return {}
        sport, league_path = LEAGUES[league]
        try:
            response = self.session.get(
                f"{self.STANDINGS_BASE}/{sport}/{league_path}/standings",
                headers=self._headers(), timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.debug("Error fetching %s standings: %s", league, e)
            return {}

        return self._parse_standings(data)

    @staticmethod
    def _parse_standings(data: Dict) -> Dict[str, str]:
        """Every team's streak, flattened out of standings' own grouping
        by division/conference -- a team is missed if only the top-level
        list is read, since none of the real entries live there.

        ESPN nests conference → division for some leagues (MLB often does),
        so this walks children recursively rather than one level deep.
        """
        out: Dict[str, str] = {}

        def walk(node: Dict) -> None:
            if not isinstance(node, dict):
                return
            for entry in ((node.get("standings") or {}).get("entries") or []):
                abbr = ascii_fold(
                    (entry.get("team") or {}).get("abbreviation", "")
                )
                if not abbr or abbr in out:
                    continue
                for stat in entry.get("stats", []) or []:
                    if stat.get("type") == "streak":
                        value = str(stat.get("displayValue") or "").strip()
                        if value and value != "-":
                            out[abbr] = value
                        break
            for child in node.get("children") or []:
                walk(child)

        walk(data or {})
        return out

    def _parse_events(self, data: Dict, league: str) -> List[Dict]:
        """Flatten ESPN's event structure into flat game dicts."""
        games = []
        for event in (data or {}).get("events", []) or []:
            competitions = event.get("competitions") or []
            if not competitions:
                continue
            comp = competitions[0]

            status = (comp.get("status") or event.get("status") or {})
            state_raw = ((status.get("type") or {}).get("state") or "").lower()
            completed = bool((status.get("type") or {}).get("completed"))

            if completed:
                state = STATE_FINAL
            elif state_raw == "in":
                state = STATE_LIVE
            else:
                state = STATE_UPCOMING

            home, away = None, None
            for competitor in comp.get("competitors", []) or []:
                side = {
                    # The team id is what the news endpoint keys on -- an
                    # abbreviation will not do -- and the scoreboard is the
                    # cheapest place to learn it, since we fetch it anyway.
                    "id": str((competitor.get("team") or {}).get("id") or ""),
                    "abbr": ascii_fold(
                        (competitor.get("team") or {}).get("abbreviation", "")
                    ),
                    "name": ascii_fold(
                        (competitor.get("team") or {}).get("shortDisplayName", "")
                    ),
                    "score": str(competitor.get("score") or ""),
                    "record": self._first_record(competitor),
                    "winner": bool(competitor.get("winner")),
                }
                if competitor.get("homeAway") == "home":
                    home = side
                else:
                    away = side

            if not home or not away:
                continue

            games.append({
                "id": str(event.get("id") or ""),
                "league": league,
                "state": state,
                "home": home,
                "away": away,
                "start": event.get("date") or "",
                "status_detail": ascii_fold(
                    (status.get("type") or {}).get("shortDetail", "")
                ),
                "clock": ascii_fold(status.get("displayClock") or ""),
                "period": status.get("period") or 0,
                "situation": self._parse_situation(comp, league),
                "broadcast": self._parse_broadcast(comp),
                # The scoreboard usually carries the leaders already. Taking
                # them here avoids a second request per game, and is the more
                # reliable source -- the summary endpoint's shape varies by
                # sport in ways the scoreboard's does not.
                "leaders": self._parse_competition_leaders(
                    dict(comp, _league=league)
                ),
            })

        return games

    @staticmethod
    def _parse_competition_leaders(comp: Dict, per_game: int = 2) -> List[Dict]:
        """Leaders carried on the scoreboard competition itself.

        For baseball this deliberately takes one from each side of the ball --
        a pitcher and a hitter -- rather than the top two entries, which are
        often both pitchers and tell you only half the story of a game.
        """
        league_hint = comp.get("_league", "")
        # A leader entry's own "team" object carries only a numeric id, not
        # an abbreviation -- confirmed against a live scoreboard response,
        # where it was just {"id": "14"}. The competitors on this same
        # competition object carry both, so resolving one to the other
        # needs no second request; it was silently returning "" for every
        # leader before this, since the field this used to read for it was
        # never actually there.
        id_to_abbr = {
            str((c.get("team") or {}).get("id") or ""):
                ascii_fold((c.get("team") or {}).get("abbreviation", ""))
            for c in comp.get("competitors", []) or []
        }
        collected = []
        for category in (comp or {}).get("leaders", []) or []:
            raw_category = (
                category.get("shortDisplayName")
                or category.get("abbreviation")
                or category.get("name", "")
            )
            # Every entry, not just the first: a category can carry one
            # performer per team, and taking only entries[0] threw away the
            # other one -- which was sometimes the only hitter available.
            for entry in (category.get("leaders") or []):
                athlete = entry.get("athlete") or {}
                full_name = ascii_fold(
                    athlete.get("displayName") or athlete.get("shortName", "")
                )
                name = abbreviate_name(full_name)
                value = ascii_fold(entry.get("displayValue", ""))
                if not name or not value:
                    continue
                team_id = str((entry.get("team") or {}).get("id") or "")
                collected.append({
                    "team": id_to_abbr.get(team_id, ""),
                    "name": name,
                    "full_name": full_name,
                    "line": value,
                    "category": readable_category(league_hint, raw_category, value),
                    "side": classify_leader(league_hint, raw_category, value),
                })

        if not collected:
            return []

        # One of each side first, then fill from whatever is left.
        chosen = []
        for side in (SIDE_PITCHING, SIDE_BATTING):
            for item in collected:
                if item["side"] == side and item not in chosen:
                    chosen.append(item)
                    break
        for item in collected:
            if len(chosen) >= per_game:
                break
            if item not in chosen:
                chosen.append(item)

        # Two identical names is a duplicate, not a second performer.
        seen_names = set()
        unique = []
        for item in chosen:
            if item["name"] in seen_names:
                continue
            seen_names.add(item["name"])
            unique.append(item)
        return unique[:per_game]

    @staticmethod
    def _parse_broadcast(comp: Dict) -> str:
        """The TV/streaming channel, from whichever field ESPN populated.

        ESPN carries this under two different keys depending on sport and
        endpoint vintage -- "broadcasts" (a list, usually with a "names"
        array) and the older singular "broadcast" (sometimes a plain
        string, sometimes a dict with "media"/"shortName"). Both are tried,
        since which one is populated is not consistent, and only the field's
        *existence* on a real competition object has been confirmed here --
        not its exact internal shape for every sport.

        A game can carry several regional feeds at once -- both sides'
        home-market broadcaster, sometimes an extra blackout/alternate
        entry -- and joining all of them read as a wall of channel names
        rather than something a viewer could act on. Only the *first*
        regional/local entry ESPN lists is kept (each competition's own
        "broadcasts" list has consistently put the relevant market first
        in the games checked so far), alongside at most one national or
        streaming entry -- one local channel plus one national/streaming
        option is what a viewer actually chooses between.
        """
        local = None
        national = None

        def add(value, market: str) -> None:
            nonlocal local, national
            name = ascii_fold(str(value)).strip()
            if not name:
                return
            # Entries with a market but no channel name used to fall through
            # to add(entry["market"]), which painted "home"/"away"/"national"
            # on the strip as if they were networks.
            if name.lower() in ("home", "away", "national", "local"):
                return
            if market in ("home", "away"):
                if local is None:
                    local = name
            elif national is None:
                national = name

        entries = comp.get("broadcasts")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                market = str(entry.get("market") or "").lower()
                names = entry.get("names")
                if isinstance(names, list):
                    for name in names:
                        if name:
                            add(name, market)
                # No names[] -- skip. Do not use market as the channel.

        if local is None and national is None:
            single = comp.get("broadcast")
            if isinstance(single, str) and single:
                add(single, "")
            elif isinstance(single, dict):
                for key in ("shortName", "name", "media", "callLetters"):
                    value = single.get(key)
                    if isinstance(value, str) and value:
                        add(value, "")
                        break

        parts = []
        for value in (local, national):
            if value and value not in parts:
                parts.append(value)
        return "/".join(parts)

    @staticmethod
    def _parse_situation(comp: Dict, league: str) -> Dict:
        """The in-progress detail a fan actually watches for.

        Each sport has its own: baseball has the count, the outs and who is
        on base; football has down and distance and who has the ball;
        basketball has the clock and the period, and little else that changes
        fast enough to be worth a line. Soccer has no ESPN "situation"
        object -- the minute and half live on the status block instead.
        """
        situation = comp.get("situation") or {}
        status = comp.get("status") or {}

        if league == "laliga":
            # Soccer competitions omit "situation" entirely; the live minute
            # still belongs beside the crests the way a basketball clock does.
            clock = ascii_fold(status.get("displayClock") or "")
            period = status.get("period") or 0
            if not clock and not period:
                return {}
            return {
                "kind": "soccer",
                "clock": clock,
                "period": period,
            }

        if not situation:
            return {}

        if league == "mlb":
            def who(key):
                # ESPN nests the athlete one level down and sometimes omits
                # it entirely between half-innings.
                block = situation.get(key) or {}
                athlete = block.get("athlete") or block
                return abbreviate_name(
                    athlete.get("shortName") or athlete.get("displayName") or ""
                )

            def count(key):
                try:
                    return int(situation.get(key) or 0)
                except (TypeError, ValueError):
                    return 0

            return {
                "kind": "baseball",
                "balls": count("balls"),
                "strikes": count("strikes"),
                "outs": count("outs"),
                "first": bool(situation.get("onFirst")),
                "second": bool(situation.get("onSecond")),
                "third": bool(situation.get("onThird")),
                "batter": who("batter"),
                "pitcher": who("pitcher"),
            }

        if league == "nfl":
            possession_id = str(situation.get("possession") or "")
            possession_abbr = ""
            for competitor in comp.get("competitors", []) or []:
                # ESPN has used both competitor.id and team.id for the
                # possession pointer across sports/seasons; match either so
                # the "*" marker does not silently vanish.
                cand = {
                    str(competitor.get("id") or ""),
                    str((competitor.get("team") or {}).get("id") or ""),
                }
                if possession_id and possession_id in cand:
                    possession_abbr = ascii_fold(
                        (competitor.get("team") or {}).get("abbreviation", "")
                    )
                    break
            return {
                "kind": "football",
                "clock": ascii_fold(status.get("displayClock", "")),
                # ESPN gives both a long and a short form; the short one is
                # what fits: "3rd & 7" rather than "3rd and 7 at NYG 42".
                "down_distance": ascii_fold(
                    situation.get("shortDownDistanceText")
                    or situation.get("downDistanceText") or ""
                ),
                "yard_line": ascii_fold(situation.get("possessionText") or ""),
                "possession": possession_abbr,
                "red_zone": bool(situation.get("isRedZone")),
            }

        return {
            "kind": "basketball",
            "clock": ascii_fold(status.get("displayClock", "")),
        }

    @staticmethod
    def _first_record(competitor: Dict) -> str:
        for record in competitor.get("records", []) or []:
            summary = record.get("summary")
            if summary:
                return str(summary)
        return ""

    # ------------------------------------------------------------------
    def fetch_leaders(self, league: str, event_id: str,
                      per_game: int = 4) -> List[Dict]:
        """Notable players for one game, with their stat line.

        ESPN's summary endpoint carries a leaders block per team -- the
        batting leader, the passing leader, the points leader, depending on
        the sport. That is exactly the "notable player and stat line" a
        scoreboard shows under a final.
        """
        if league not in LEAGUES or not event_id:
            return []
        sport, league_path = LEAGUES[league]

        try:
            response = self.session.get(
                f"{self.BASE}/{sport}/{league_path}/summary",
                params={"event": event_id},
                headers=self._headers(), timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.debug("No summary for %s/%s: %s", league, event_id, e)
            return []

        # Soccer scoreboard competitions carry no leaders block; the summary
        # does, but goal scorers live in keyEvents and are what a fan
        # actually wants under a BAR (or any La Liga) card. Prefer those,
        # then fall back to summary leaders (shots etc.).
        if league == "laliga":
            scorers = self._parse_soccer_scorers(data, per_game)
            if scorers:
                return scorers
        return self._parse_leaders(data, per_game, league)

    @staticmethod
    def _parse_soccer_scorers(data: Dict, per_game: int = 4) -> List[Dict]:
        """Goal scorers from summary keyEvents (scoringPlay=true)."""
        id_to_abbr = {}
        for competitor in (
            ((data.get("header") or {}).get("competitions") or [{}])[0]
            .get("competitors") or []
        ):
            team = competitor.get("team") or {}
            tid = str(team.get("id") or "")
            abbr = ascii_fold(team.get("abbreviation", ""))
            if tid and abbr:
                id_to_abbr[tid] = abbr

        out = []
        for event in data.get("keyEvents") or []:
            if not event.get("scoringPlay"):
                continue
            participants = event.get("participants") or []
            if not participants:
                continue
            athlete = (participants[0].get("athlete") or {})
            full_name = ascii_fold(
                athlete.get("displayName") or athlete.get("shortName", "")
            )
            name = abbreviate_name(full_name)
            if not name:
                continue
            clock = ascii_fold(
                ((event.get("clock") or {}).get("displayValue") or "")
            )
            team_id = str((event.get("team") or {}).get("id") or "")
            team_abbr = id_to_abbr.get(team_id, "")
            kind = ((event.get("type") or {}).get("text") or "Goal")
            line = clock or ascii_fold(kind)
            out.append({
                "team": team_abbr,
                "name": name,
                "full_name": full_name,
                "line": line,
                "category": "GOAL",
                "side": SIDE_BATTING,
            })
            if len(out) >= per_game:
                break
        return out

    @staticmethod
    def _parse_leaders(data: Dict, per_game: int = 4, league: str = "mlb") -> List[Dict]:
        """Every leader the summary carries, classified by side of the ball.

        The scoreboard picks one headline performer per team, and in baseball
        that is nearly always the pitcher -- which is why an offensive line
        never appeared. The summary lists each category separately, so a
        hitter can actually be found.
        """
        out = []
        for team_block in (data or {}).get("leaders", []) or []:
            team_abbr = ascii_fold(
                (team_block.get("team") or {}).get("abbreviation", "")
            )
            for category in team_block.get("leaders", []) or []:
                entries = category.get("leaders") or []
                if not entries:
                    continue
                entry = entries[0]
                athlete = entry.get("athlete") or {}
                full_name = ascii_fold(
                    athlete.get("displayName") or athlete.get("shortName", "")
                )
                name = abbreviate_name(full_name)
                value = ascii_fold(entry.get("displayValue", ""))
                if not name or not value:
                    continue
                raw = (category.get("shortDisplayName")
                       or category.get("abbreviation")
                       or category.get("name", ""))
                out.append({
                    "team": team_abbr,
                    "name": name,
                    "full_name": full_name,
                    "line": value,
                    "category": readable_category(league, raw, value),
                    "side": classify_leader(league, raw, value),
                })
        return out

    @staticmethod
    def _parse_boxscore_batting(data: Dict) -> List[Dict]:
        """The best hitter for each team, read from the boxscore.

        Baseball summaries carry no "leaders" block at all -- the scoreboard
        supplies a single rated performer per game, and when that is a pitcher
        there is no offensive line anywhere else to find. The boxscore lists
        every batter, so the hitter is derived here instead: most hits, then
        home runs, then runs batted in.
        """
        out = []
        for team_block in ((data or {}).get("boxscore") or {}).get("players", []) or []:
            team_abbr = ascii_fold(
                (team_block.get("team") or {}).get("abbreviation", "")
            )
            for group in team_block.get("statistics", []) or []:
                if (group.get("name") or "").lower() != "batting":
                    continue

                labels = [str(l).upper() for l in (group.get("labels") or [])]
                def column(*names):
                    for wanted in names:
                        if wanted in labels:
                            return labels.index(wanted)
                    return None

                idx_h, idx_hr = column("H"), column("HR")
                idx_rbi, idx_r = column("RBI"), column("R")
                idx_ab = column("AB")

                best, best_score = None, -1
                for athlete_row in group.get("athletes", []) or []:
                    stats = athlete_row.get("stats") or []

                    def value(index):
                        if index is None or index >= len(stats):
                            return 0
                        try:
                            return int(str(stats[index]).split("-")[0] or 0)
                        except (TypeError, ValueError):
                            return 0

                    hits, homers = value(idx_h), value(idx_hr)
                    rbi, runs = value(idx_rbi), value(idx_r)
                    at_bats = value(idx_ab)
                    if at_bats <= 0 and hits <= 0:
                        continue

                    # Weighted so a home run outranks a single, and a hit
                    # outranks a walk-and-run line.
                    score = hits * 3 + homers * 4 + rbi * 2 + runs
                    if score <= 0 or score <= best_score:
                        continue

                    athlete = athlete_row.get("athlete") or {}
                    full_name = ascii_fold(
                        athlete.get("displayName") or athlete.get("shortName", "")
                    )
                    name = abbreviate_name(full_name)
                    if not name:
                        continue

                    parts = [f"{hits}-{at_bats}"] if at_bats else []
                    if homers:
                        parts.append(f"{homers} HR" if homers > 1 else "HR")
                    if rbi:
                        parts.append(f"{rbi} RBI")
                    if not parts:
                        continue

                    best_score = score
                    best = {
                        "team": team_abbr,
                        "name": name,
                        "full_name": full_name,
                        "line": ", ".join(parts),
                        "category": "BAT",
                        "side": SIDE_BATTING,
                    }

                if best:
                    out.append(best)
        return out

    def fetch_batting(self, league: str, event_id: str) -> List[Dict]:
        """Best hitter per team for one game, from the boxscore."""
        if league != "mlb" or not event_id:
            return []
        sport, league_path = LEAGUES[league]
        try:
            response = self.session.get(
                f"{self.BASE}/{sport}/{league_path}/summary",
                params={"event": event_id},
                headers=self._headers(), timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.debug("No boxscore for %s: %s", event_id, e)
            return []
        return self._parse_boxscore_batting(data)

    @staticmethod
    def pick_performer(game: Dict, focus_abbr: str) -> Optional[Dict]:
        """The one performance worth showing for a given team's board.

        An offensive line from the followed team -- what did our hitters do --
        and if that team lost, the winner's instead, because the story of a
        loss is who beat you. Falls back to any line rather than showing
        nothing.
        """
        leaders = [l for l in (game.get("leaders") or []) if l.get("name")]
        if not leaders:
            return None

        wanted = abbr_group(focus_abbr)
        home = game.get("home") or {}
        away = game.get("away") or {}

        focus_won = any(
            side.get("winner") and side.get("abbr", "").upper() in wanted
            for side in (home, away)
        )
        if game.get("state") == "final" and not focus_won:
            winner = next(
                (s for s in (home, away) if s.get("winner")), None
            )
            if winner:
                wanted = abbr_group(winner.get("abbr", ""))

        def matches(leader):
            return leader.get("team", "").upper() in wanted

        for predicate in (
            lambda l: matches(l) and l.get("side") == SIDE_BATTING,
            lambda l: l.get("side") == SIDE_BATTING,
            matches,
            lambda l: True,
        ):
            for leader in leaders:
                if predicate(leader):
                    return leader
        return None

    @staticmethod
    def day_abbr(when: datetime, *, today: bool = False) -> str:
        """Three-letter weekday in Title Case -- same as the forecast columns.

        Forecast days render as "Mon", "Tue", …; fixture and clock labels
        used to force ALL CAPS ("MON", "TDY"), which read as a different
        voice on the same strip. "Tdy" keeps the today marker at three
        letters in that same casing.
        """
        if today:
            return "Tdy"
        # %a is already Title Case in English locales; .title() still
        # normalises any ALL-CAPS locale oddity to Mon/Tue/…
        return (when.strftime("%a") or "")[:3].title()

    @staticmethod
    def local_start(game: Dict) -> str:
        """When the next game is, in the shortest form that is unambiguous.

            today       "Tdy 8/9 7:05"
            any other    "Mon 8/11 7:05"

        Weekday, date and time together. The weekday alone repeats every
        seven days, and a bare clock time is ambiguous on a board you glance
        at, so both are always present.
        """
        raw = game.get("start") or ""
        if not raw:
            return ""
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        except Exception:
            return ""

        # %-I is not portable; strip the leading zero by hand.
        hour = when.strftime("%I").lstrip("0") or "12"
        clock = f"{hour}:{when.strftime('%M')}"

        today = datetime.now().astimezone().date()
        delta = (when.date() - today).days

        # Day name and date together, always. The weekday alone repeats every
        # seven days and the date alone makes you count -- a board you glance
        # at needs both.
        day = ESPNGamesSource.day_abbr(when, today=(delta == 0))
        return f"{day} {when.month}/{when.day} {clock}"
