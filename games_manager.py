"""
Games Manager

Fetches and caches games for the configured teams, and decides how often to
refetch.

Cadence is the whole point of this class. A final from last night does not
change; a game in progress changes every pitch or possession. So a live game
is refreshed on a short timer and everything else on a long one, and the
render path never touches the network.
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

from espn_data_source import (
    ESPNGamesSource,
    DEFAULT_TEAMS,
    LEAGUES,
    abbr_group,
    STATE_FINAL,
    STATE_LIVE,
    STATE_UPCOMING,
)


class GamesManager:
    """Holds the current set of games for the configured teams."""

    def __init__(
        self,
        logger: logging.Logger,
        teams: Optional[List[Dict]] = None,
        cache_manager=None,
        idle_interval: int = 60,
        live_interval: int = 5,
        leaders_per_game: int = 2,
        fetch_leaders: bool = True,
    ):
        self.logger = logger
        self.teams = teams or list(DEFAULT_TEAMS)
        self.cache_manager = cache_manager
        self.idle_interval = idle_interval
        self.live_interval = live_interval
        self.leaders_per_game = leaders_per_game
        self.fetch_leaders = fetch_leaders

        try:
            self.source = ESPNGamesSource(logger)
        except Exception as e:
            self.logger.warning("Could not start the ESPN source: %s", e)
            self.source = None

        self._games: List[Dict] = []
        self._other_live: List[Dict] = []
        self._fetched_at: float = 0.0
        self._leaders_fetched: set = set()
        self._team_ids: Dict[str, str] = {}
        # league -> last time a far-future lookup ran for it. A team with
        # nothing in the normal window (an NBA team in August, for
        # instance) used to vanish from the board entirely -- no banner,
        # nothing -- since the strip itself is keyed off "teams that have a
        # game". This is checked once a day at
        # most, since finding a fixture months out does not need refreshing
        # on the same cadence as an actual live game.
        self._next_game_checked: Dict[str, float] = {}
        self._next_game_interval: float = 86400.0
        # "league:abbr" -> the found far-future fixture. self._games is
        # rebuilt from scratch on every refresh, so without this a fixture
        # found on one refresh would vanish again on the very next one,
        # since the throttle above deliberately skips searching again --
        # the whole point of finding it is that it keeps showing up.
        self._far_future_cache: Dict[str, Dict] = {}
        # A found far-future fixture is cached (and its own daily lookup
        # throttled) the moment it's located, but only actually surfaced
        # onto the strip once it's this close -- a banner for a game 120
        # days out reads as stale or wrong, not as "this team is playing
        # soon".
        self._far_future_show_window: float = 5 * 86400.0

        # abbr -> "W3"/"L2", from the league standings rather than tallied
        # from recent finals -- ESPN already computes it, and the normal
        # game fetch only keeps a day or two of history, nowhere near
        # enough to tally a streak from scratch.
        self._streaks: Dict[str, str] = {}
        self._streaks_at: Dict[str, float] = {}

        self._restore()

    # ------------------------------------------------------------------
    @property
    def _cache_key(self) -> str:
        return "local_scoreboard_games"

    def _restore(self) -> None:
        """Warm from disk so a restart shows something immediately."""
        if not self.cache_manager:
            return
        try:
            cached = self.cache_manager.get(self._cache_key)
            if isinstance(cached, dict) and cached.get("games"):
                self._games = cached["games"]
                self._fetched_at = float(cached.get("at", 0))
                self.logger.debug("Restored %d games from cache", len(self._games))
        except Exception as e:
            self.logger.debug("Could not restore games: %s", e)

    def _persist(self) -> None:
        if not self.cache_manager:
            return
        try:
            self.cache_manager.set(
                self._cache_key, {"games": self._games, "at": self._fetched_at}
            )
        except Exception as e:
            self.logger.debug("Could not persist games: %s", e)

    # ------------------------------------------------------------------
    def _team_index(self) -> Dict[str, set]:
        """league -> every spelling of every followed abbreviation.

        Expanded through the alias table, so a team configured as NYK still
        matches a feed that says NY, and vice versa. A mismatch here is
        invisible -- it produces an empty board, not an error -- so it is
        worth being generous.
        """
        index: Dict[str, set] = {}
        for team in self.teams:
            league = team.get("league")
            abbr = (team.get("abbr") or "").upper()
            if league in LEAGUES and abbr:
                index.setdefault(league, set()).update(abbr_group(abbr))
        return index

    def _is_followed(self, game: Dict, index: Dict[str, set]) -> bool:
        wanted = index.get(game.get("league"), set())
        return (
            (game.get("home") or {}).get("abbr", "").upper() in wanted
            or (game.get("away") or {}).get("abbr", "").upper() in wanted
        )

    def _find_far_future_games(
        self, index: Dict[str, set], collected: List[Dict]
    ) -> List[Dict]:
        """One fixture for any followed team the normal window found nothing
        for, so it still gets a banner instead of vanishing.

        The normal fetch looks 7 days ahead, which is plenty in-season but
        finds nothing for a team on a long break -- an NBA team in August is
        the obvious case, months before its opener. A team with zero games
        was being dropped from the strip entirely, since the strip itself
        is built from "teams that have a game". This
        looks up to 120 days ahead instead, confirmed against a live request
        to actually reach the next season's opener, but only for a league
        that came up genuinely empty for a followed team, and at most once a
        day per league -- self._far_future_cache is what makes a found
        fixture keep appearing on the refreshes that throttle skips, since
        self._games is rebuilt from scratch every time.
        """
        covered = set()
        for g in collected:
            covered.add((g.get("league"), (g.get("home") or {}).get("abbr", "").upper()))
            covered.add((g.get("league"), (g.get("away") or {}).get("abbr", "").upper()))

        missing = [
            team for team in self.teams
            if team.get("league") in LEAGUES
            and not any(
                (team.get("league"), spelling) in covered
                for spelling in abbr_group(team.get("abbr", ""))
            )
        ]
        if not missing:
            return []

        now = time.time()
        for league in {team.get("league") for team in missing}:
            last = self._next_game_checked.get(league, 0.0)
            if now - last < self._next_game_interval:
                continue
            try:
                games = self.source.fetch_scoreboard(
                    league, days_back=0, days_forward=120
                )
            except Exception as e:
                self.logger.debug("Far-future lookup failed for %s: %s", league, e)
                continue
            # Only stamp after a real response. None means the request failed
            # -- throttling that for 24h left an off-season followed team
            # missing after a single blip. An empty list is a successful
            # "nothing in the window" and should still throttle.
            if games is None:
                continue
            self._next_game_checked[league] = now
            upcoming = sorted(
                (g for g in games if self._is_followed(g, index)
                 and g.get("state") == STATE_UPCOMING),
                key=lambda g: g.get("start", ""),
            )
            picked_ids = set()
            for team in missing:
                if team.get("league") != league:
                    continue
                wanted = abbr_group(team.get("abbr", ""))
                for g in upcoming:
                    if g["id"] in picked_ids:
                        continue
                    if (g["home"]["abbr"].upper() in wanted
                            or g["away"]["abbr"].upper() in wanted):
                        key = f"{league}:{team.get('abbr', '').upper()}"
                        self._far_future_cache[key] = g
                        picked_ids.add(g["id"])
                        break
            if picked_ids:
                self.logger.info(
                    "%s: nothing in the normal window, found %d game(s) "
                    "up to 120 days out", league, len(picked_ids)
                )

        # Found and cached as soon as the wide lookup locates it, but not
        # surfaced onto the strip until it's within _far_future_show_window
        # of its own start -- an off-season team's opener found 120 days out
        # is not "the team has a game" in any sense a reader glancing at the
        # board would recognise; it reads as a stale/wrong fixture sitting
        # there for months. The cache keeps the lookup itself from repeating
        # every day regardless of whether the game is shown yet.
        now_ts = time.time()
        extra = []
        for team in missing:
            key = f"{team.get('league')}:{team.get('abbr', '').upper()}"
            game = self._far_future_cache.get(key)
            if not game:
                continue
            raw = game.get("start") or ""
            try:
                start_ts = datetime.fromisoformat(
                    raw.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if start_ts - now_ts <= self._far_future_show_window:
                extra.append(game)
        return extra

    def team_id(self, team: Dict) -> str:
        """ESPN's numeric id for a team, learned from the scoreboard."""
        wanted = abbr_group(team.get("abbr", ""))
        for game in self._games:
            if game.get("league") != team.get("league"):
                continue
            for side in (game.get("home") or {}, game.get("away") or {}):
                if side.get("abbr", "").upper() in wanted and side.get("id"):
                    return side["id"]
        return ""

    def has_live(self) -> bool:
        return any(g.get("state") == STATE_LIVE for g in self._games)

    def has_any_live(self) -> bool:
        """A followed team's own game, or anything else live around the
        league -- broader than has_live() on purpose. Season stats
        competing with a live score for the same scroll is the problem
        either way, whether that live score belongs to a followed team or
        not; _other_live is already filtered to STATE_LIVE at collection
        time, so nothing further to check there."""
        return self.has_live() or bool(self._other_live)

    def _interval(self) -> int:
        """Short timer while any game is playing -- or a followed one is
        about to.

        has_any_live(), not has_live(): refresh() fetches followed teams
        and other-live games together in one call, not on two
        independently-timed schedules, so an other-live game with no
        followed team also live still needs the fast timer to actually
        stay current -- otherwise "live around the league" sat on the
        slow idle cadence regardless of how urgently it needed updating.

        has_any_live() only ever reflects the *previous* fetch. A game
        that crosses from upcoming to live in between two long,
        idle-interval checks would otherwise sit undetected for most of
        that gap -- up to idle_interval itself, since nothing shortens the
        timer until a refresh actually happens to notice. Checking each
        followed game's own start time closes that gap for a followed
        team specifically: the short timer kicks in shortly before first
        pitch and stays until either the state flips to live (has_live()
        then keeps it short on its own) or the game is well past its
        scheduled start without one, at which point a delay or
        postponement is as likely as a slow status flip and the idle timer
        is the sane default again. There's no equivalent check across
        every unfollowed team in every league -- an other-live game only
        speeds things up once it's actually live.
        """
        if self.has_any_live() or self._followed_game_starting_soon():
            return self.live_interval
        return self.idle_interval

    def _followed_game_starting_soon(self, pre_window: float = 600.0,
                                     post_window: float = 14400.0) -> bool:
        """A followed team's own upcoming game starts within `pre_window`
        seconds, or was due to start within the last `post_window`
        seconds but our last fetch still shows it upcoming.

        post_window is generous (4 hours) on purpose: a rain delay does
        not move a baseball game's "start" field, so a real, still-coming
        game can otherwise look identical to a postponement from here.
        Reverting to the idle timer too early would reintroduce the exact
        gap this method exists to close, just for delayed games instead
        of on-time ones -- confirmed against real hardware once already,
        when a live game sat undetected because the timer had already
        gone back to idle. A genuine postponement to another day still
        gets caught eventually, once ESPN republishes a new start time
        and this same window logic re-evaluates against it.
        """
        now = time.time()
        for game in self._games:
            if game.get("state") != STATE_UPCOMING:
                continue
            raw = game.get("start") or ""
            if not raw:
                continue
            try:
                start_ts = datetime.fromisoformat(
                    raw.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if -post_window <= (start_ts - now) <= pre_window:
                return True
        return False

    def is_stale(self) -> bool:
        return (time.time() - self._fetched_at) >= self._interval()

    # ------------------------------------------------------------------
    def refresh(self, force: bool = False) -> None:
        """Refetch every followed league, plus every league's live games.

        Safe to call every tick: it returns immediately while the cache is
        warm, so this costs nothing on the hot path.

        Two collections come out of the same fetch. The followed-team games
        are the board's main content, in every state. The "other live"
        collection is deliberately narrower: it exists to answer "what else
        is live right now", not "what else is scheduled" -- a final from a
        team you don't follow is not news, but a live one might be worth a
        glance. All three leagues are checked for it regardless of which
        ones your teams are in, since the point is what's live around the
        league, not around your roster.
        """
        if not self.source:
            return
        if not force and not self.is_stale():
            return

        index = self._team_index()
        collected: List[Dict] = []
        other_live: List[Dict] = []
        failed_leagues = set()

        for league in LEAGUES:
            games = self.source.fetch_scoreboard(league)
            # None = request failed. Keep that league's previous rows rather
            # than treating failure as "no games" and wiping good data when
            # another league still returns fine.
            if games is None:
                failed_leagues.add(league)
                self.logger.warning(
                    "%s scoreboard fetch failed; keeping previous games", league
                )
                continue
            followed = [g for g in games if self._is_followed(g, index)]
            collected.extend(followed)

            unfollowed_live = [
                g for g in games
                if g.get("state") == STATE_LIVE and g not in followed
            ]
            other_live.extend(unfollowed_live)

            self.logger.debug(
                "%s: %d games, %d involve a followed team, %d other live",
                league, len(games), len(followed), len(unfollowed_live),
            )

        if failed_leagues:
            kept_ids = {g.get("id") for g in collected}
            for g in self._games:
                if g.get("league") in failed_leagues and g.get("id") not in kept_ids:
                    collected.append(g)
                    kept_ids.add(g.get("id"))
            other_ids = {g.get("id") for g in other_live}
            for g in self._other_live:
                if g.get("league") in failed_leagues and g.get("id") not in other_ids:
                    other_live.append(g)
                    other_ids.add(g.get("id"))

        if not collected and not other_live:
            # Keep whatever we had rather than blanking the board on a
            # transient failure; the timestamp is not advanced, so the next
            # tick retries.
            self.logger.warning("No games returned; keeping previous data")
            return

        collected.extend(self._find_far_future_games(index, collected))

        collected.sort(key=self._sort_key)
        other_live.sort(key=lambda g: (g.get("league", ""), g.get("start", "")))
        self._games = collected
        self._other_live = other_live
        self._fetched_at = time.time()
        self._persist()

        live = sum(1 for g in collected if g["state"] == STATE_LIVE)
        final = sum(1 for g in collected if g["state"] == STATE_FINAL)
        upcoming = sum(1 for g in collected if g["state"] == STATE_UPCOMING)
        self.logger.info(
            "Refreshed games: %d live, %d final, %d upcoming, "
            "%d other live around the league (next check %ds)",
            live, final, upcoming, len(other_live), self._interval(),
        )

        if self.fetch_leaders:
            self._refresh_leaders()
            self._refresh_other_live_leaders()

    def _refresh_other_live_leaders(self) -> None:
        """Performer highlights for other-live games, live only.

        These are transient by nature -- a game leaves this list the moment
        it stops being live -- so there is no cache-once-and-remember step
        the way finals get; every refresh simply asks again for whichever
        games are currently in the list.
        """
        for game in self._other_live:
            if game.get("state") != STATE_LIVE:
                continue
            key = f"{game['league']}:{game['id']}"
            try:
                if game["league"] == "mlb":
                    hitters = self.source.fetch_batting(game["league"], game["id"])
                else:
                    hitters = self.source.fetch_leaders(
                        game["league"], game["id"], self.leaders_per_game
                    )
            except Exception as e:
                self.logger.debug("Performer lookup failed for %s: %s", key, e)
                continue
            if hitters:
                existing = game.get("leaders") or []
                seen = {(l.get("name"), l.get("line")) for l in existing}
                merged = list(existing)
                for hitter in hitters:
                    stamp = (hitter.get("name"), hitter.get("line"))
                    if stamp not in seen:
                        seen.add(stamp)
                        merged.append(hitter)
                game["leaders"] = merged

    def _refresh_leaders(self) -> None:
        """Attach performers to games that have been played.

        The scoreboard supplies one rated performer per game, which in
        baseball is often a pitcher. When no batting line is present, the
        boxscore is read instead -- baseball summaries carry no leaders block
        at all, so that is the only place a hitter can be found.

        NFL / NBA / soccer get a summary fetch even when the scoreboard
        already carried a composite rating, so the board shows PTS / PASS /
        GOAL lines rather than an opaque "RAT". Finals are remembered once
        a usable line lands; an empty miss is retried next refresh.
        """
        for game in self._games:
            state = game.get("state")
            if state == STATE_UPCOMING:
                continue

            league = game.get("league") or ""
            key = f"{league}:{game['id']}"
            if state == STATE_FINAL and key in self._leaders_fetched:
                continue

            existing = game.get("leaders") or []
            if league == "mlb" and any(l.get("side") == "batting" for l in existing):
                if state == STATE_FINAL:
                    self._leaders_fetched.add(key)
                continue

            hitters = []
            try:
                if league == "mlb":
                    hitters = self.source.fetch_batting(league, game["id"])
                else:
                    hitters = self.source.fetch_leaders(
                        league, game["id"], self.leaders_per_game
                    )
            except Exception as e:
                self.logger.debug("Performer lookup failed for %s: %s", key, e)
                continue

            if hitters:
                merged = list(existing)
                seen = {(l.get("name"), l.get("line")) for l in merged}
                for hitter in hitters:
                    stamp = (hitter.get("name"), hitter.get("line"))
                    if stamp not in seen:
                        seen.add(stamp)
                        merged.append(hitter)
                game["leaders"] = merged
                self.logger.debug(
                    "%s: %d performer(s), %d batting", key, len(merged),
                    sum(1 for l in merged if l.get("side") == "batting"),
                )

            # Only stamp a final once we have something to show, so a
            # transient empty summary does not blank the note forever.
            if state == STATE_FINAL and (hitters or game.get("leaders")):
                self._leaders_fetched.add(key)

    def refresh_streaks(self, interval: float = 300.0) -> None:
        """Each followed league's standings, on a slow timer.

        A streak only changes once a final actually lands, and standings
        move far more slowly than a score -- there is no reason to fetch
        this on the same cadence as live games, or even every time update()
        runs. One request per followed league, not per team: standings
        already return the whole league's teams in one response.

        Five minutes, not thirty: a streak sitting visibly one game behind
        for up to half an hour after a game actually goes final read as
        stale/wrong to a viewer checking right after the last out, and a
        standings request is cheap enough that five minutes costs nothing
        extra worth avoiding.
        """
        if not self.source:
            return
        now = time.time()
        leagues = {team.get("league") for team in self.teams if team.get("league")}
        for league in leagues:
            if now - self._streaks_at.get(league, 0.0) < interval:
                continue
            try:
                found = self.source.fetch_standings(league)
            except Exception as e:
                self.logger.debug("Streaks failed for %s: %s", league, e)
                continue
            self._streaks_at[league] = now
            if found:
                self._streaks.update(found)

    def streak_for(self, team: Dict) -> str:
        # Standings keys use ESPN's spelling; config may use an alias
        # (ARI vs AZ). Match through the same group table as followed-team
        # detection so a streak does not silently vanish.
        for spelling in abbr_group(team.get("abbr", "")):
            value = self._streaks.get(spelling, "")
            if value:
                return value
        return ""

    @staticmethod
    def _sort_key(game: Dict):
        """Live first, then upcoming by start time, then finals most recent."""
        order = {STATE_LIVE: 0, STATE_UPCOMING: 1, STATE_FINAL: 2}
        return (order.get(game.get("state"), 3), game.get("start", ""))

    # ------------------------------------------------------------------
    def games_for_team(self, team: Dict) -> List[Dict]:
        """Every game involving one team, live first then finals then fixtures.

        Grouping by team is the point of the strip layout: a Yankees score
        followed by a Knicks score followed by a Giants fixture never builds
        a picture of any of them.
        """
        league = team.get("league")
        wanted = abbr_group(team.get("abbr", ""))
        order = {STATE_LIVE: 0, STATE_FINAL: 1, STATE_UPCOMING: 2}
        found = [
            g for g in self._games
            if g.get("league") == league
            and (
                (g.get("home") or {}).get("abbr", "").upper() in wanted
                or (g.get("away") or {}).get("abbr", "").upper() in wanted
            )
        ]
        found.sort(key=lambda g: (order.get(g.get("state"), 3), g.get("start", "")))
        return found

    def teams_with_games(self) -> List[Dict]:
        """Teams that have something to show, those playing now first.

        A game in progress is the one thing on this board that will not keep.
        Leaving a live team wherever it happened to sit in the configured
        order means waiting most of a pass to see a score that is changing as
        you wait.

        Teams with a configured favorite_player stay on the strip even with
        no headline games, so a kid's star (Yamal on Barça) still appears
        on quiet days.
        """
        having = [
            t for t in self.teams
            if self.games_for_team(t) or (t.get("favorite_player") or "").strip()
        ]
        live_first = [
            t for t in having
            if any(g.get("state") == STATE_LIVE for g in self.games_for_team(t))
        ]
        rest = [t for t in having if t not in live_first]
        return live_first + rest

    def headline_games(self, team: Dict) -> List[Dict]:
        """What a glance at a team should show: now, last, next.

        Deliberately at most three -- a live game if there is one, the most
        recent completed game, and the next scheduled one. A week of fixtures
        and a fortnight of results is a database, not a scoreboard.
        """
        games = self.games_for_team(team)
        live = [g for g in games if g.get("state") == STATE_LIVE]

        finals = [g for g in games if g.get("state") == STATE_FINAL]
        # Most recently started first, so "last game" means the latest one.
        finals.sort(key=lambda g: g.get("start", ""), reverse=True)

        upcoming = [g for g in games if g.get("state") == STATE_UPCOMING]
        upcoming.sort(key=lambda g: g.get("start", ""))

        out = list(live)
        if finals:
            out.append(finals[0])
        if upcoming:
            out.append(upcoming[0])
        return out

    def games(self, state: Optional[str] = None) -> List[Dict]:
        if state is None:
            return list(self._games)
        return [g for g in self._games if g.get("state") == state]

    def has_data(self) -> bool:
        return bool(self._games)

    def other_live_games(self, limit: Optional[int] = None,
                         followed_leagues_only: bool = False,
                         per_league_limit: int = 0) -> List[Dict]:
        """Live games around the league that do not involve a followed team.

        Deliberately live-only, not the full schedule: this exists to answer
        "what else is live right now", and a final or a fixture for a team
        you do not follow is not something this board should spend space on.
        """
        games = [g for g in self._other_live if g.get("state") == STATE_LIVE]
        if followed_leagues_only:
            leagues = {t.get("league") for t in self.teams if t.get("league")}
            games = [g for g in games if g.get("league") in leagues]
        if per_league_limit and per_league_limit > 0:
            kept: List[Dict] = []
            counts: Dict[str, int] = {}
            for g in games:
                league = g.get("league") or ""
                if counts.get(league, 0) >= per_league_limit:
                    continue
                counts[league] = counts.get(league, 0) + 1
                kept.append(g)
            games = kept
        return games[:limit] if limit else games
