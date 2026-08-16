"""
Local Scoreboard for LEDMatrix

One board for a handful of teams across several leagues: live scores, final
results and upcoming games, with team logos and the notable performer from
each game, alongside local weather, a clock, moon phase and personal
countdowns -- all fully configurable, not tied to any one city or roster.

The default team list, weather coordinates and countdown dates ship as a
starting example, not a requirement -- every one of them is plain
configuration, not code, so this runs equally well for any city and any
followed teams.

Display modes:
    local_live      games in progress
    local_recent    finished games
    local_upcoming  scheduled games

Why this exists alongside baseball-scoreboard: that plugin covers MLB
thoroughly, one league at a time. This one answers a different question --
"are any of my teams playing right now" -- across every league at once.
"""

import logging
import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from src.plugin_system.base_plugin import BasePlugin
except ImportError:
    BasePlugin = None

from espn_data_source import (
    DEFAULT_TEAMS, ESPNGamesSource, LEAGUES, abbr_group,
    STATE_FINAL, STATE_LIVE, STATE_UPCOMING,
)
from games_manager import GamesManager
from game_renderer import GameCardRenderer
from strip_renderer import StripRenderer
from logo_manager import TeamLogoManager
from weather_source import NWSWeather
from leaders_data_source import ALL_CATEGORIES, LEAGUE_SCOPES
from leaders_manager import BaseballLeadersManager
from awards_manager import AWARD_DEFINITIONS, BaseballAwardsManager
import countdowns

logger = logging.getLogger(__name__)

MODE_STATE = {
    "local_live": STATE_LIVE,
    "local_recent": STATE_FINAL,
    "local_upcoming": STATE_UPCOMING,
}

# The scrolling mode shows one team at a time with everything about that team
# on a single strip, rather than cycling card by card across all of them.
MODE_TEAMS = "local_scoreboard"


class LocalScoreboardPlugin(BasePlugin if BasePlugin else object):
    """Scores and schedules for a set of followed teams."""

    # Names this plugin writes to. Checked against the base class at startup:
    # BasePlugin exposes read-only properties, and assigning to one raises
    # inside __init__, which the loader reports as a failed instantiation.
    _OWNED_ATTRS = (
        "teams_config", "teams_game_duration", "teams_max_visit",
        "teams_show_logos", "teams_show_leaders", "teams_idle_interval",
        "teams_live_interval", "teams_leaders_per_game",
        "teams_layout", "teams_scroll_speed",
        "teams_leaderboards_on", "teams_leader_categories",
        "teams_leader_scopes", "teams_leader_depth", "teams_award_keys",
        "teams_weather_on", "teams_weather_point", "teams_weather_label",
        "teams_weather_units", "teams_weather_interval",
        "teams_panel_on", "teams_panel_team", "teams_panel_width",
        "teams_panel_priority", "teams_panel_phase",
        "teams_other_live_on", "teams_other_live_limit",
        "teams_countdowns_on", "teams_countdown_events", "teams_countdown_limit",
    )

    def __init__(self, plugin_id: str, config: Dict[str, Any], display_manager,
                 cache_manager, plugin_manager):
        if BasePlugin:
            super().__init__(plugin_id, config, display_manager,
                             cache_manager, plugin_manager)

        self.plugin_id = plugin_id
        self.config = config or {}
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.plugin_manager = plugin_manager
        self.logger = logger

        try:
            self.display_width = display_manager.matrix.width
            self.display_height = display_manager.matrix.height
        except AttributeError:
            self.display_width = getattr(display_manager, "width", 128)
            self.display_height = getattr(display_manager, "height", 32)

        self._check_attribute_collisions()

        self._init_error = None
        try:
            self._apply_config(self.config)
            self._build_components()
        except Exception as e:
            self._init_error = e
            self.logger.error(
                "Local Scoreboard failed to initialise and will stay disabled: %s",
                e, exc_info=True,
            )
            self._safe_defaults()

        self._index: Dict[str, int] = {}
        self._team_index = 0
        self._scroll_offset = 0.0
        self._scroll_key = None
        self._scroll_last_ts = 0.0
        # A fingerprint of every live game's own score and situation, as of
        # the last frame. adopt_pending() normally only swaps in a rebuilt
        # strip at the scroll seam, once per full pass -- fine for cosmetic
        # changes, but on a long strip that lap can take minutes, during
        # which a live game starting/ending (leaderboards/awards/countdowns
        # should hide or reappear) or an already-live game's own score,
        # count or batter changing would all sit stale on screen despite
        # the data itself refreshing underneath every few seconds. Any
        # change to this fingerprint forces an out-of-turn adopt the
        # moment the rebuild is ready, instead of waiting for the lap to
        # finish.
        self._last_live_signature = None
        self._urgent_adopt = False
        # Leaderboard and award segments are rebuilt on a timer, not per
        # frame; the data behind them refetches every few hours.
        self._boards_cache = None
        self._boards_built = 0.0
        self._boards_interval = 60.0
        self._boards_titles = None
        self._seen: Dict[str, set] = {}
        self._mode_started: Dict[str, float] = {}
        self._last_advance = 0.0
        self._current_mode: Optional[str] = None
        self._last_update = 0.0
        self._drew_placeholder = False
        # See update()/_dispatch_background_update's own comment: the host
        # framework holds a per-plugin lock for the duration of update(),
        # and skips that plugin's display() entirely while it's held --
        # the panel just shows its last frame. The actual data fetch this
        # method used to do inline is real network I/O (confirmed on the
        # Pi: ~1s per cycle with a normal number of live games), which
        # made the display visibly freeze for a large fraction of every
        # live_interval. Backgrounding it here fixes that at the source,
        # the same way strip_renderer.py backgrounds strip composition.
        self._refresh_lock = threading.Lock()
        self._refresh_in_flight = False
        self._refresh_thread: Optional[threading.Thread] = None

        if self.renderer is not None:
            self.logger.info("Local Scoreboard initialised: %s",
                             self.renderer.profile.describe())
            self.logger.info("Fonts: %s", self.renderer.font_report())

    # ------------------------------------------------------------------
    def _check_attribute_collisions(self) -> None:
        clashes = []
        for name in self._OWNED_ATTRS:
            for base in type(self).__mro__[1:]:
                descriptor = getattr(base, name, None)
                if isinstance(descriptor, property) and descriptor.fset is None:
                    clashes.append(name)
                    break
        if clashes:
            self.logger.error(
                "Attribute name collision with the base plugin class: %s",
                ", ".join(clashes),
            )

    def _apply_config(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        self.is_enabled = self.config.get("enabled", True)
        self.enabled = self.is_enabled

        self.display_duration = float(self.config.get("display_duration", 30))
        self.teams_game_duration = float(self.config.get("game_duration", 12))
        self.teams_max_visit = float(self.config.get("max_display_duration", 60))

        self.teams_idle_interval = int(self.config.get("idle_interval", 60))
        self.teams_live_interval = int(self.config.get("live_interval", 5))
        self.teams_leaders_per_game = int(self.config.get("leaders_per_game", 2))

        teams = self.config.get("teams")
        self.teams_config = teams if isinstance(teams, list) and teams else list(DEFAULT_TEAMS)

        # Live games around the league, outside the followed teams. Live
        # only, on purpose: a final or a fixture from a team you don't
        # follow is not news, but a live one might be worth a glance.
        other_cfg = self.config.get("other_live_games", {})
        self.teams_other_live_on = other_cfg.get("enabled", True)
        self.teams_other_live_limit = int(other_cfg.get("limit", 5))
        self.teams_other_live_followed_leagues_only = bool(
            other_cfg.get("followed_leagues_only", False)
        )
        self.teams_other_live_per_league_limit = int(
            other_cfg.get("per_league_limit", 0) or 0
        )

        # Days until a configured, recurring annual date -- a birthday, a
        # holiday. Empty by default, same as star_players once was: these
        # are personal to whoever is running this board and should not be
        # guessed at.
        countdown_cfg = self.config.get("countdowns", {})
        self.teams_countdowns_on = countdown_cfg.get("enabled", True)
        self.teams_countdown_events = countdown_cfg.get("events") or []
        self.teams_countdown_limit = int(countdown_cfg.get("limit", 3))

        weather_cfg = self.config.get("weather", {})
        self.teams_weather_on = weather_cfg.get("enabled", True)
        self.teams_weather_point = (
            float(weather_cfg.get("latitude", 40.6687)),
            float(weather_cfg.get("longitude", -74.1143)),
        )
        self.teams_weather_label = weather_cfg.get("label", "Bayonne")
        self.teams_weather_units = weather_cfg.get("units", "F")
        self.teams_weather_interval = float(weather_cfg.get("interval", 900))
        # Off by default -- the original design keeps the whole weather
        # block up during a live game on purpose (a warning is more urgent
        # than a live score). This only hides the moon phase and forecast
        # columns, current conditions stay up regardless, and only for
        # whoever turns it on; installs that don't set this keep today's
        # behavior unchanged.
        self.teams_weather_hide_forecast_when_live = bool(
            weather_cfg.get("hide_forecast_when_live", False)
        )

        # A fixed panel for one team's live game, on the leftmost module.
        panel_cfg = self.config.get("static_panel", {})
        self.teams_panel_on = panel_cfg.get("enabled", True)
        # An ordered preference, not a single team: when two followed teams
        # are playing at once something has to win the module, and picking by
        # whichever the schedule happened to list first is arbitrary.
        priority = panel_cfg.get("priority")
        if not priority:
            single = panel_cfg.get("team")
            priority = [single] if single else [
                t.get("abbr") for t in self.teams_config
            ]
        self.teams_panel_priority = [str(a).upper() for a in priority if a]
        self.teams_panel_team = (
            self.teams_panel_priority[0] if self.teams_panel_priority else "NYY")
        self.teams_panel_width = int(panel_cfg.get("width", 64))
        self.teams_panel_phase = max(2.0, float(panel_cfg.get("alternate", 5)))

        card = self.config.get("customization", {}).get("card", {})
        self.teams_show_logos = card.get("show_logos", True)
        self.teams_show_leaders = card.get("show_leaders", True)

        # "strip" scrolls one team's whole story past; "cards" shows one game
        # at a time. Strip is the default: it keeps a team's information
        # together, which is the thing card-by-card rotation cannot do.
        self.teams_layout = self.config.get("layout", "strip")
        self.teams_scroll_speed = float(self.config.get("scroll_speed", 22))
        self.teams_rivalry_live_boost = max(
            0, min(3, int(self.config.get("rivalry_live_boost", 1)))
        )
        self.teams_rivalry_scroll_factor = max(
            0.3, min(1.0, float(self.config.get("rivalry_scroll_factor", 0.7)))
        )
        self.teams_kid_friendly = bool(self.config.get("kid_friendly", False))

        # League leaders ride on the same strip, so the board shows scores and
        # leaderboards in one scroll rather than handing between two plugins.
        leaders_cfg = self.config.get("leaderboards", {})
        self.teams_leaderboards_on = leaders_cfg.get("enabled", True)
        self.teams_leader_categories = leaders_cfg.get(
            "categories", ["homeRuns", "battingAverage", "earnedRunAverage"]
        )
        # Per league, not MLB-wide: an AL home run leader and an NL one are
        # different races, and merging them hides both.
        split = leaders_cfg.get("scope", "al_nl")
        self.teams_leader_scopes = {
            "mlb": ["mlb"], "al": ["al"], "nl": ["nl"],
            "al_nl": ["al", "nl"],
        }.get(split, ["al", "nl"])
        self.teams_leader_depth = int(leaders_cfg.get("depth", 3))
        self.teams_award_keys = [
            a for a in leaders_cfg.get(
                "awards", ["mvp", "cy_young", "roy", "triple_crown"])
            if a in AWARD_DEFINITIONS
        ]

    def _build_components(self) -> None:
        self.games = GamesManager(
            logger=self.logger,
            teams=self.teams_config,
            cache_manager=self.cache_manager,
            idle_interval=self.teams_idle_interval,
            live_interval=self.teams_live_interval,
            leaders_per_game=self.teams_leaders_per_game,
            fetch_leaders=self.teams_show_leaders,
        )
        self.logos = TeamLogoManager(
            logger=self.logger, allow_download=self.teams_show_logos
        )
        self.renderer = GameCardRenderer(
            display_manager=self.display_manager, config=self.config,
            logger=self.logger, logo_manager=self.logos,
        )
        self.strip = StripRenderer(
            display_manager=self.display_manager, config=self.config,
            logger=self.logger, logo_manager=self.logos,
            # A background *thread* still shares this process's GIL, so
            # composing a rebuilt strip there still measurably starved the
            # real render loop of its own CPU time -- confirmed on the Pi
            # as a periodic scroll pause no amount of throttling or
            # caching fully removed. A separate process has its own GIL,
            # so it genuinely cannot block this one's bytecode the way a
            # thread could. See _compose_worker_main's own docstring.
            use_process=True,
        )
        self.weather = (
            NWSWeather(
                logger=self.logger,
                latitude=self.teams_weather_point[0],
                longitude=self.teams_weather_point[1],
                label=self.teams_weather_label,
                units=self.teams_weather_units,
            ) if self.teams_weather_on else None
        )
        self._weather_data = {}
        self._weather_at = 0.0

        self.leaders = BaseballLeadersManager(
            logger=self.logger,
            cache_manager=self.cache_manager,
            cache_duration=int(self.config.get("leaderboards", {})
                               .get("cache_duration", 21600)),
            leaders_per_category=max(2, self.teams_leader_depth),
            # A leaderboard row itself only ever shows rank, name and the
            # one value it ranked in, but the season MVP note needs a full
            # line (AVG/HR/RBI, not just whichever single category the
            # player happened to lead) -- one extra HTTP call per (group,
            # scope) on the same slow cache cycle, a rounding error next to
            # what it buys.
            fetch_player_stats=True,
        ) if self.teams_leaderboards_on else None
        self.awards = (
            BaseballAwardsManager(
                logger=self.logger, leaders_manager=self.leaders,
                top_n=self.teams_leader_depth,
            ) if self.leaders is not None else None
        )

    def _safe_defaults(self) -> None:
        """Enough state that every method the core calls is safe."""
        self.is_enabled = False
        self.enabled = False
        self.display_duration = 30.0
        self.teams_game_duration = 12.0
        self.teams_max_visit = 60.0
        self.teams_config = []
        self.teams_show_logos = False
        self.teams_show_leaders = False
        self.teams_idle_interval = 60
        self.teams_live_interval = 5
        self.teams_leaders_per_game = 0
        self.teams_layout = "strip"
        self.teams_scroll_speed = 22.0
        self.teams_rivalry_live_boost = 0
        self.teams_rivalry_scroll_factor = 1.0
        self.teams_kid_friendly = False
        self.teams_leaderboards_on = False
        self.teams_leader_categories = []
        self.teams_leader_scopes = []
        self.teams_leader_depth = 3
        self.teams_award_keys = []
        self.teams_weather_on = False
        self.teams_weather_point = (0.0, 0.0)
        self.teams_weather_label = ""
        self.teams_weather_units = "F"
        self.teams_weather_interval = 900.0
        self.teams_weather_hide_forecast_when_live = False
        self.teams_panel_on = False
        self.teams_panel_team = "NYY"
        self.teams_panel_priority = []
        self.teams_panel_phase = 5.0
        self.teams_other_live_on = False
        self.teams_other_live_limit = 5
        self.teams_other_live_followed_leagues_only = False
        self.teams_other_live_per_league_limit = 0
        self.teams_countdowns_on = False
        self.teams_countdown_events = []
        self.teams_countdown_limit = 3
        self.teams_panel_width = 64
        self.weather = None
        self._weather_data = {}
        self._weather_at = 0.0
        self.games = None
        self.logos = None
        self.renderer = None
        self.strip = None
        self.leaders = None
        self.awards = None

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        """Apply edits live. Cached games are kept; timers are not.

        Editing settings is how someone says "this looks wrong", so the next
        tick refetches rather than serving whatever is cached.
        """
        # Let any in-flight refresh / strip compose finish against the old
        # managers before we replace them -- otherwise the background thread
        # can write into an abandoned GamesManager while the new one keeps
        # a stale copy of _games only.
        self._wait_for_background_update(timeout=2.0)
        previous_strip = getattr(self, "strip", None)
        if previous_strip is not None:
            previous_strip._wait_for_background_build(timeout=2.0)

        previous = getattr(self, "games", None)
        self._apply_config(new_config)
        self._build_components()
        if previous is not None and self.games is not None:
            self.games._games = list(previous._games)
            self.games._other_live = list(getattr(previous, "_other_live", []) or [])
            self.games._streaks = dict(getattr(previous, "_streaks", {}) or {})
            self.games._far_future_cache = dict(
                getattr(previous, "_far_future_cache", {}) or {})
            self.games._next_game_checked = dict(
                getattr(previous, "_next_game_checked", {}) or {})
            self.games._fetched_at = getattr(previous, "_fetched_at", 0.0)
        self._last_update = 0.0
        self._index.clear()
        self._seen.clear()
        self._mode_started.clear()
        self._boards_cache = None
        self._boards_built = 0.0
        self.logger.info("Local Scoreboard config reloaded")

    # ------------------------------------------------------------------
    @property
    def needs_high_fps(self) -> bool:
        """Told to the display controller directly, rather than left to its
        enable_scrolling fallback heuristic: the strip layout is one
        continuously scrolling image, and the framework's own high-FPS
        loop (125 FPS) is what makes that motion smooth rather than
        stepping visibly. Cards layout swaps a static frame in on its own
        schedule instead -- there is no motion for a high frame rate to
        smooth out there, so it stays at the framework's normal cadence.
        """
        return self.teams_layout == "strip"

    def get_available_modes(self) -> List[str]:
        if not self.is_enabled or self.games is None:
            return []
        if self.teams_layout == "strip":
            return [MODE_TEAMS] if self.games.teams_with_games() else []
        # One mode in either layout. Declaring the card modes too meant the
        # controller offered them, the plugin declined, and the panel held its
        # last frame through three dead slots -- which reads as a freeze.
        return [MODE_TEAMS] if any(
            self.games.games(state) for state in MODE_STATE.values()) else []

    def _games_for(self, mode: str) -> List[Dict]:
        if self.games is None:
            return []
        state = MODE_STATE.get(mode)
        if state is None:
            return self.games.games()      # the single mode shows everything
        games = self.games.games(state)
        # Upcoming games are only interesting a few deep; the rest is noise.
        if state == STATE_UPCOMING:
            games = games[: int(self.config.get("upcoming_limit", 5))]
        return games

    # ------------------------------------------------------------------
    def _panel_game(self):
        """The live game (if any) currently pinned to the static panel,
        and the followed abbreviation it matched.

        Shared by update() (to decide how often to refetch) and
        _display_strip() (to decide what to draw) so the two can never
        disagree about whether something is actually pinned right now.
        """
        if not self.teams_panel_on or self.games is None:
            return None, ""
        live = self.games.games(STATE_LIVE)
        for abbr in self.teams_panel_priority:
            wanted = abbr_group(abbr)
            for candidate in live:
                sides = (candidate.get("home") or {}, candidate.get("away") or {})
                if any(s.get("abbr", "").upper() in wanted for s in sides):
                    return candidate, abbr
        return None, ""

    def update(self) -> None:
        if not self.is_enabled or self.games is None:
            return
        now = time.time()
        # Checks for a live game every idle_interval (a minute by default);
        # once ANY game is live -- has_any_live(), not has_live(), on
        # purpose -- that check itself drops to live_interval (5s) so a
        # game's own numbers -- balls, strikes, score -- stay current
        # instead of sitting frozen for most of a minute. Confirmed on
        # real hardware that the old, much slower live cadence (45s) left
        # an at-bat's count looking unchanged. has_live() alone missed the
        # "live around the league" case entirely: refresh() is one call
        # that fetches followed teams and other-live games together, not
        # two independently-timed fetches, so an other-live game with no
        # followed team also live sat on the slow idle cadence regardless
        # of how urgently it needed updating -- there is no such thing as
        # "everything else keeps its own interval" here, contrary to what
        # an earlier version of this comment claimed.
        # _followed_game_starting_soon() covers the same gap for a
        # followed team's game about to start: has_live() only reflects
        # the previous fetch, so without it a game beginning near the end
        # of an idle-interval wait could go undetected for most of that
        # wait too. There's no equivalent "starting soon" check across
        # every unfollowed team in every league -- that's a much larger
        # scan for a narrower benefit, so an other-live game only speeds
        # up once it's actually live, the same one-fetch-behind gap
        # has_live() alone has for a followed team's own game.
        fast = (
            self.games.has_any_live()
            or self.games._followed_game_starting_soon()
        )
        gate = self.teams_live_interval if fast else self.teams_idle_interval
        if now - self._last_update < gate and self.games.has_data():
            return
        # Only advance the gate when a refresh actually starts. Stamping
        # before a single-flight no-op burned the interval and left live
        # scores stale for ~2x live_interval on a slow fetch day.
        if self._dispatch_background_update(fast):
            self._last_update = now

    def _dispatch_background_update(self, fast: bool) -> bool:
        """Runs the actual data refresh -- games, streaks, leaders, weather,
        logos -- on a background thread, so update() itself always returns
        almost immediately.

        The host framework's plugin_manager holds a per-plugin lock for the
        duration of update(), and skips that plugin's display() entirely
        while update() is still running -- the panel just shows its last
        pushed frame rather than a new one. That's invisible as long as
        update() is fast, but the real work here is genuine network I/O:
        confirmed directly against the Pi, games.refresh() alone took
        ~1 second per call with a normal live-game load, every
        live_interval (5s) -- meaning the display was frozen for roughly a
        fifth of every five seconds, worse on a day with more live games to
        fetch. Threading is enough here, unlike strip_renderer.py's move to
        a separate *process* for composition: that was working around GIL
        contention from real CPU-bound drawing work competing with the
        render thread, but a network call releases the GIL while it waits,
        so a background thread costs the render loop nothing.

        Single-flight, guarded by _refresh_lock: update()'s own gate
        already limits how often this is called, but a slow network day
        could in principle still have one dispatch still running when the
        next would-be dispatch arrives, and starting a second one
        concurrently would only make the network contention worse, not
        fix anything. Returns True when a new thread was started, False
        when one was already in flight (caller must not burn the gate).

        GamesManager.refresh() and friends stay fully synchronous
        themselves -- deliberately. Every test in test_offline.py that
        calls games.refresh() depends on it being finished by the time the
        call returns; making it asynchronous by default would break that
        contract for everything that already works. This wraps the call
        from the outside instead, the same relationship
        _dispatch_background_build has to _compose_strip in
        strip_renderer.py.
        """
        with self._refresh_lock:
            if self._refresh_in_flight:
                return False
            self._refresh_in_flight = True

        def _run() -> None:
            try:
                self.games.refresh(force=fast)
                self.games.refresh_streaks()
                if self.leaders is not None:
                    # Season leaders move slowly; the manager's own cache
                    # decides when a refetch is actually due.
                    needs_rookies = "roy" in self.teams_award_keys
                    for scope in self.teams_leader_scopes:
                        for group in ("hitting", "pitching"):
                            self.leaders.refresh(group, scope)
                            if needs_rookies:
                                self.leaders.refresh(group, scope, pool="rookie")

                    # A team whose best player never cracks a league-wide
                    # leaderboard's top N gets no MVP from team_best() at
                    # all -- most followed teams, most of the time. Only
                    # fetch a whole roster (a much bigger request than the
                    # leaderboards themselves) for a team that actually
                    # needs it, and never from the render path --
                    # refresh_team_roster is throttled on its own slow
                    # cache, same as everything else here.
                    if self.awards is not None:
                        for team in self.teams_config:
                            if team.get("league") != "mlb":
                                continue
                            abbr = team.get("abbr", "")
                            has_league_mvp = any(
                                self.awards.team_best(abbr, scope)
                                for scope in (self.teams_leader_scopes or ["mlb"])
                            )
                            if not has_league_mvp:
                                self.leaders.refresh_team_roster(abbr)

                if self.weather is not None:
                    weather_now = time.time()
                    if weather_now - self._weather_at >= self.teams_weather_interval:
                        fetched = self.weather.fetch()
                        if fetched:
                            self._weather_data = fetched
                            self._weather_at = weather_now
                            alerts = fetched.get("alerts") or []
                            self.logger.info(
                                "Weather: %s%s, %s%s",
                                fetched.get("now_temp", "?"),
                                fetched.get("units", ""),
                                fetched.get("now_condition", ""),
                                f" [{alerts[0]['event']}]" if alerts else "",
                            )

                if self.teams_show_logos and self.logos is not None:
                    # Include ESPN team id so soccer/La Liga crests warm
                    # correctly (CDN is numeric-id keyed, not abbr).
                    pairs = {
                        (g["league"], side["abbr"],
                         str(side.get("id") or ""))
                        for g in self.games.games()
                        for side in (g["away"], g["home"])
                        if side.get("abbr")
                    }
                    if pairs:
                        self.logos.prefetch(sorted(pairs),
                                            max(6, self.display_height // 4))
            except Exception as e:
                self.logger.error("Error updating games: %s", e, exc_info=True)
            finally:
                with self._refresh_lock:
                    self._refresh_in_flight = False

        thread = threading.Thread(target=_run, daemon=True,
                                  name="scoreboard-update")
        self._refresh_thread = thread
        thread.start()
        return True

    def _wait_for_background_update(self, timeout: float = 5.0) -> bool:
        """Block until any in-flight background update() dispatch finishes.

        Never called from update()/display() themselves -- the whole point
        of backgrounding is that nothing on that path waits on it. Exists
        for tests, which need a deterministic point to synchronize on
        rather than a real wall-clock race against a daemon thread -- the
        same relationship StripRenderer._wait_for_background_build has to
        its own background compose.
        """
        thread = self._refresh_thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    # ------------------------------------------------------------------
    def display(self, display_mode: str = None, force_clear: bool = False) -> bool:
        if not self.is_enabled or self.renderer is None:
            return False

        if self.teams_layout == "strip":
            return self._display_strip()

        display_mode = display_mode if display_mode in MODE_STATE else MODE_TEAMS
        games = self._games_for(display_mode)

        if not games:
            if not self._drew_placeholder and (
                    self.games is None or not self.games.has_data()):
                self._drew_placeholder = True
                return self.renderer.draw_message("Loading")
            return False

        self._drew_placeholder = False

        if display_mode != self._current_mode:
            self._current_mode = display_mode
            self._last_advance = time.time()
            self._mode_started.setdefault(display_mode, time.time())

        self._advance_if_due(display_mode, games)

        index = self._index.get(display_mode, 0) % len(games)
        game = dict(games[index])
        self._seen.setdefault(display_mode, set()).add(game.get("id"))

        if game.get("state") == STATE_UPCOMING and self.games is not None:
            game["start_label"] = ESPNGamesSource.local_start(game)

        self.logger.debug(
            "CARD %s %s %s@%s (%d/%d)", display_mode, game.get("league"),
            game.get("away", {}).get("abbr"), game.get("home", {}).get("abbr"),
            index + 1, len(games),
        )
        return self.renderer.draw_game(game)

    def _leaderboards(self):
        """Leader segments and award segments, per league, ready to draw.

        Returned separately so each can carry its own banner: a leaderboard is
        a fact, an award watch list is a computed opinion, and the board should
        not present them as the same kind of thing.

        Cached. This is called from the render path, which runs every frame,
        and computing the award standings is a full weighted scoring pass over
        every leaderboard.
        """
        if self.leaders is None or not self.teams_leaderboards_on:
            return [], []

        now = time.time()
        if (self._boards_cache is not None
                and now - self._boards_built < self._boards_interval
                and getattr(self, "_boards_kid_mode", None) is self.teams_kid_friendly):
            return self._boards_cache

        # AL and NL still get their own mark leading the list (confirmed
        # ESPN actually serves one for each, at a different path than a
        # real league's own mark), plus the "AL"/"NL" text back in the
        # title itself. "MLB" (the merged scope) has no separate mark of
        # its own here, so it always carried the text label regardless.
        boards, awards = [], []
        for scope in self.teams_leader_scopes:
            label = LEAGUE_SCOPES.get(scope, {}).get("label", "")
            for category in self.teams_leader_categories:
                rows = self.leaders.get_category(category, scope)
                if not rows:
                    continue
                stat = ALL_CATEGORIES.get(category, {}).get(
                    "label", category.upper())
                if self.teams_kid_friendly:
                    # Full words kids can read: "AL Home Runs", not "AL HR Leaders".
                    kid_stat = {
                        "homeRuns": "Home Runs",
                        "hits": "Hits",
                        "stolenBases": "Stolen Bases",
                        "runsBattedIn": "RBIs",
                        "runs": "Runs",
                        "battingAverage": "Batting Avg",
                        "earnedRunAverage": "ERA",
                        "wins": "Wins",
                        "strikeouts": "Strikeouts",
                    }.get(category, stat)
                    title = f"{label} {kid_stat}".strip()
                    header = kid_stat
                else:
                    title = f"{label} {stat} Leaders".strip()
                    header = stat
                boards.append((
                    title, rows[: self.teams_leader_depth], header, scope,
                ))

        if self.awards is not None:
            for scope in self.teams_leader_scopes:
                label = LEAGUE_SCOPES.get(scope, {}).get("label", "")
                for key in self.teams_award_keys:
                    definition = AWARD_DEFINITIONS.get(key) or {}
                    candidates = self.awards.compute(key, scope)
                    if not candidates:
                        continue
                    # No value column: diagnose_award_odds.py confirmed there
                    # is no structured, ToS-clean source for individual award
                    # odds -- ESPN's futures endpoint covers only team-level
                    # markets. A computed weighted score dressed up with a
                    # number and a header ("SCR", then "PROJ") kept reading as
                    # more authoritative than it is. The ranked order is the
                    # real information; the row simply omits "value" so the
                    # renderer draws no column and no header for it.
                    rows = [{
                        "rank": c.get("rank"),
                        "short_name": c.get("short_name") or c.get("name"),
                        "team": c.get("team", ""),
                    } for c in candidates[: self.teams_leader_depth]]
                    award_label = definition.get("label", key)
                    if self.teams_kid_friendly:
                        award_label = {
                            "mvp": "MVP Race",
                            "cy_young": "Best Pitcher",
                            "roy": "Rookie Race",
                            "triple_crown": "Triple Crown",
                        }.get(key, award_label)
                    title = f"{label} {award_label}".strip()
                    awards.append((title, rows, scope))

        self._boards_cache = (boards, awards)
        self._boards_built = now
        self._boards_kid_mode = self.teams_kid_friendly
        titles = [entry[0] for entry in boards + awards]
        if titles != self._boards_titles:
            self._boards_titles = titles
            self.logger.info(
                "Strip sections (%d): %s", len(titles), ", ".join(titles) or "none"
            )
        return boards, awards

    @staticmethod
    def _format_stat_line(stats: Dict[str, str], group: str) -> str:
        """AVG/HR/RBI for a hitter, ERA/W/K for a pitcher -- the player's
        whole season, not just whichever single category they happened to
        rank in.
        """
        fields = ["AVG", "HR", "RBI"] if group == "hitting" else ["ERA", "W", "K"]
        parts = [f"{f} {stats[f]}" for f in fields if f in stats]
        return "  ".join(parts)

    def _stat_line(self, player_id: str, group: str, scope: str) -> str:
        if self.leaders is None or not player_id:
            return ""
        return self._format_stat_line(
            self.leaders.get_player_stats(player_id, group, scope), group)

    def _countdowns(self) -> List[Dict]:
        """Days until each configured date, soonest first."""
        if not self.teams_countdowns_on or not self.teams_countdown_events:
            return []
        return countdowns.upcoming(
            self.teams_countdown_events, datetime.now().date(),
            self.teams_countdown_limit,
        )

    def _streaks(self) -> Dict[str, str]:
        """Each followed team's current win/loss streak, keyed by
        abbreviation -- omitted for a team with none to report yet."""
        if self.games is None:
            return {}
        out = {}
        for team in self.teams_config:
            streak = self.games.streak_for(team)
            if streak:
                out[team.get("abbr", "")] = streak
        return out

    def _team_mvps(self) -> Dict[str, Dict[str, str]]:
        """Each followed MLB team's own standout this season, with a real
        stat line. NBA and NFL have no leaderboard data behind them here,
        so this is MLB-only.

        Tries the league-wide ranking first (team_best() -- a player who
        already ranks in a league-wide leaderboard's top N). Most followed
        teams have nobody there most of the time, which used to leave most
        teams with no entry at all; a team with nothing there falls back to
        its own roster, ranked against nobody but its own players. That
        roster is never fetched from here -- update() decides which teams
        actually need one and refreshes it on its own slow cache, since the
        render path must never touch the network.
        """
        if self.awards is None or not self.teams_leaderboards_on:
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for team in self.teams_config:
            if team.get("league") != "mlb":
                continue
            abbr = team.get("abbr", "")

            best, scope = None, "mlb"
            for scope in self.teams_leader_scopes or ["mlb"]:
                best = self.awards.team_best(abbr, scope)
                if best:
                    break
            if best:
                line = self._stat_line(
                    best.get("player_id", ""), best.get("group", "hitting"), scope
                )
                if line:
                    out[abbr] = {
                        "name": best.get("name", ""),
                        "short_name": best["short_name"],
                        "line": line,
                    }
                    continue

            if self.leaders is None:
                continue
            hitting, pitching, names, full_names = self.leaders.get_team_roster(abbr)
            if not hitting and not pitching:
                continue
            roster_best = self.awards.team_mvp_from_roster(
                hitting, pitching, names, full_names)
            if not roster_best:
                continue
            group = roster_best.get("group", "hitting")
            stats = (hitting if group == "hitting" else pitching).get(
                roster_best.get("player_id", ""), {})
            line = self._format_stat_line(stats, group)
            if not line:
                continue
            out[abbr] = {
                "name": roster_best.get("name", ""),
                "short_name": roster_best.get("short_name", ""),
                "line": line,
            }
        return out

    @staticmethod
    def _live_signature(teams_and_games, other_live, panel_game=None) -> tuple:
        """A fingerprint of every currently-live game's own score and
        situation -- balls, strikes, outs, down and distance, clock, the
        leader lines ESPN attaches. Anything that changes what a viewer
        watching that specific game would actually see changing.

        Deliberately not the same as the strip's own build-signature: that
        one also reflects finals, fixtures, leaderboard rows and every
        other slower-moving thing on the strip, so comparing it frame to
        frame would call almost every refresh "urgent". This narrows to
        just what a live game means by "updated every 5 seconds" -- the
        game itself, not the strip around it.

        panel_game is passed separately and deliberately: _display_strip()
        excludes a followed team's own live game from teams_and_games once
        it's pinned to the static panel, so showing it twice doesn't waste
        the scroll -- but that exclusion also made it invisible here,
        which meant the single most common live-game case (your own
        followed team, pinned) never counted as a change at all. Its own
        panel repaints every frame regardless of the scroll, so this
        wasn't a stale-panel bug, but it did mean show_clock's own
        transition, and any_live's leaderboard/awards/countdown hide,
        still wasn't reaching the scroll until the next natural seam.
        """
        def fingerprint(g):
            situation = g.get("situation") or {}
            return (
                g.get("id"), g.get("state"),
                (g.get("home") or {}).get("score"),
                (g.get("away") or {}).get("score"),
                situation.get("balls"), situation.get("strikes"),
                situation.get("outs"), situation.get("down_distance"),
                situation.get("clock"), situation.get("period"),
                tuple(l.get("line", "") for l in (g.get("leaders") or [])),
            )

        followed = [
            fingerprint(g)
            for _, games in teams_and_games for g in games
            if g.get("state") == STATE_LIVE
        ]
        if panel_game is not None:
            followed.append(fingerprint(panel_game))
        other = tuple(sorted(fingerprint(g) for g in (other_live or [])))
        return (tuple(sorted(followed)), other)

    def _display_strip(self) -> bool:
        """Scroll one continuous strip carrying every team, and wrap.

        Called once per frame, so the offset advances by real elapsed time
        rather than a fixed step -- the pace is then identical whether the Pi
        renders at 30fps or 110.
        """
        # A live game for the pinned team holds the left module; everything
        # else scrolls past it. Only that team, and only while it is playing.
        panel_game, panel_abbr = self._panel_game()

        if panel_game is not None:
            # The bottom row alternates on a slow beat so both the situation
            # and the players involved get their turn.
            phase = int(time.time() // self.teams_panel_phase)
            self.strip.set_static_panel(self.strip.render_static_panel(
                panel_game, panel_abbr, self.teams_panel_width, phase))
        elif self.teams_panel_on:
            # Nothing live to pin -- the left module would otherwise sit
            # unused while the strip scrolls past it. The clock and current
            # temperature go there instead, so the time is always visible
            # somewhere without ever touching the scroll itself: this is
            # the same static-panel slot, just a lower-priority thing to
            # put in it. A live game still wins outright the moment there
            # is one.
            self.strip.set_static_panel(self.strip.render_clock_weather_panel(
                datetime.now(), self._weather_data, self.teams_panel_width))
        else:
            self.strip.set_static_panel(None)

        teams = self.games.teams_with_games() if self.games else []
        if not teams:
            if not self._drew_placeholder:
                self._drew_placeholder = True
                return self.strip.draw_message("Loading")
            return False
        self._drew_placeholder = False

        # Now, last and next per team -- not the whole schedule.
        teams_and_games = [(t, self.games.headline_games(t)) for t in teams]
        if panel_game is not None:
            # Already fixed to the left module; showing it twice wastes the
            # strip and looks like a duplicate.
            teams_and_games = [
                (t, [g for g in games if g.get("id") != panel_game.get("id")])
                for t, games in teams_and_games
            ]
        teams_and_games = [(t, g) for t, g in teams_and_games
                           if g or (t.get("favorite_player") or "").strip()]
        other_live = (
            self.games.other_live_games(
                self.teams_other_live_limit,
                followed_leagues_only=self.teams_other_live_followed_leagues_only,
                per_league_limit=self.teams_other_live_per_league_limit,
            )
            if self.teams_other_live_on else []
        )
        # The panel absorbs a followed live game out of the scroll. When that
        # game was the only headline left, teams_and_games goes empty -- and
        # returning False here used to leave the panel image set in memory
        # without ever calling draw_strip, so nothing painted (confirmed:
        # one followed live team + other-live elsewhere). Keep going whenever
        # there is still a panel, other-live, or any remaining scroll content.
        if not teams_and_games and not other_live and panel_game is None:
            return False

        labels = {
            g["id"]: ESPNGamesSource.local_start(g)
            for _, games in teams_and_games for g in games
            if g.get("state") == STATE_UPCOMING
        }

        # Still computed (and its own cache kept warm) even while hidden --
        # the moment the last live game ends, the very next frame already
        # has fresh data ready rather than a cold-cache delay. Hidden
        # rather than never-fetched: a live score is the thing this board
        # exists to show right now, and everything else here (season
        # stats, a birthday countdown) competing for the same scroll only
        # pushes it further away -- has_any_live() on purpose, not
        # has_live(): a live game from outside the followed teams is still
        # a live game competing for the same attention.
        any_live = self.games.has_any_live()
        boards, awards = self._leaderboards()
        countdown_events = self._countdowns()
        if any_live:
            boards, awards, countdown_events = [], [], []
        weather_show_forecast = not (
            any_live and self.teams_weather_hide_forecast_when_live
        )
        streaks = self._streaks()

        live_signature = self._live_signature(
            teams_and_games, other_live, panel_game)
        if (self._last_live_signature is not None
                and live_signature != self._last_live_signature):
            self._urgent_adopt = True
        self._last_live_signature = live_signature

        # The clock only belongs in the scroll when it isn't already
        # pinned to the left module -- showing it in both places at once
        # is the same time twice. It's pinned there whenever nothing live
        # has taken that spot over (see render_clock_weather_panel above),
        # so the scroll only needs its own copy while a live game has
        # bumped the clock out of the static slot.
        show_clock = panel_game is not None
        built = self.strip.build_strip(
            teams_and_games, labels, leaderboards=boards, awards=awards,
            weather=self._weather_data,
            clock=datetime.now(),
            other_live=other_live,
            team_mvps=self._team_mvps(),
            countdowns=countdown_events,
            streaks=streaks,
            weather_show_forecast=weather_show_forecast,
            show_clock=show_clock,
            weather_show_current=show_clock,
            rivalry_live_boost=self.teams_rivalry_live_boost,
        )
        if built is None:
            return False
        if (self.teams_kid_friendly
                and not getattr(self, "_fun_art_logged", False)
                and getattr(self.strip, "_flyers_enabled", lambda: False)()):
            self._fun_art_logged = True
            self.logger.info(
                "Fun art: plumber flyovers with depth "
                "(closer mid-screen, farther at edges)"
            )
        span = self.strip.scroll_span(built)

        now = time.time()
        if self._scroll_key is None:
            self._scroll_key = "strip"
            self._scroll_last_ts = now
            self._mode_started.setdefault(MODE_TEAMS, now)
            self.logger.debug(
                "STRIP rebuilt: %d teams, %dpx", len(teams_and_games), span
            )

        elapsed = max(0.0, min(1.0, now - self._scroll_last_ts))
        self._scroll_last_ts = now
        speed = self._effective_scroll_speed(teams_and_games, panel_game)
        self._scroll_offset += elapsed * speed

        reached_seam = bool(span and self._scroll_offset >= span)
        if reached_seam:
            # A full pass. Wrap rather than stop -- the strip is continuous.
            self._scroll_offset -= span
            self._seen.setdefault(MODE_TEAMS, set()).add("pass")

        # The seam is normally the only moment nothing is mid-view, so a
        # rebuilt strip is adopted there rather than the instant the data
        # changed -- swapping mid-pass shifts every segment after the
        # changed one sideways, which reads as a glitch for an ordinary
        # score update. A live game starting or every live game ending is
        # different: it's what leaderboards/awards/countdowns hiding or
        # reappearing hinges on, and on a long strip the current pass can
        # take minutes, during which the board keeps showing content that
        # should already be gone (or stays hidden after it should be back).
        # That transition jumps the queue -- adopted the moment the
        # rebuild is ready, seam or not.
        if reached_seam or self._urgent_adopt:
            if self.strip.adopt_pending():
                self._urgent_adopt = False
                built = self.strip.build_strip(
                    teams_and_games, labels, leaderboards=boards, awards=awards,
                    weather=self._weather_data,
                    clock=datetime.now(),
                    other_live=other_live,
                    team_mvps=self._team_mvps(),
                    countdowns=countdown_events,
                    streaks=streaks,
                    weather_show_forecast=weather_show_forecast,
                    show_clock=show_clock,
                    weather_show_current=show_clock,
                    rivalry_live_boost=self.teams_rivalry_live_boost,
                )
                self._scroll_offset = 0.0
                self.logger.debug(
                    "Adopted rebuilt strip at the seam" if reached_seam else
                    "Adopted rebuilt strip immediately for a live-state change"
                )

        # Repaint just the clock box; recomposing the strip for a minute
        # change would cost hundreds of milliseconds.
        self.strip.refresh_clock(datetime.now())
        self.strip.refresh_fun_art(time.time())
        return self.strip.draw_strip(built, self._scroll_offset)

    def _effective_scroll_speed(self, teams_and_games, panel_game=None) -> float:
        """Base scroll speed, slowed while a followed rivalry game is live."""
        speed = max(1.0, self.teams_scroll_speed)
        factor = getattr(self, "teams_rivalry_scroll_factor", 1.0)
        if factor >= 0.999:
            return speed
        if panel_game is not None:
            for team in self.teams_config:
                rivals = team.get("rivals") or []
                if not rivals:
                    continue
                if self._game_is_rivalry(
                        panel_game, focus_abbr=team.get("abbr", ""),
                        rivals=rivals):
                    return max(1.0, speed * factor)
        for team, games in teams_and_games or []:
            rivals = team.get("rivals") or []
            if not rivals:
                continue
            for game in games:
                if game.get("state") == STATE_LIVE and self._game_is_rivalry(
                        game, focus_abbr=team.get("abbr", ""), rivals=rivals):
                    return max(1.0, speed * factor)
        return speed

    @staticmethod
    def _game_is_rivalry(game: Dict, focus_abbr: str = "",
                         rivals: Optional[List] = None) -> bool:
        if rivals is None:
            return False
        home = (game.get("home") or {}).get("abbr", "")
        away = (game.get("away") or {}).get("abbr", "")
        wanted = abbr_group(focus_abbr) if focus_abbr else set()
        if wanted and home.upper() in wanted:
            theirs = away
        elif wanted and away.upper() in wanted:
            theirs = home
        else:
            theirs = away
        rival_abbrs = set()
        for a in rivals:
            rival_abbrs |= abbr_group(str(a))
        return bool(rival_abbrs and abbr_group(theirs) & rival_abbrs)

    def _advance_if_due(self, mode: str, games: List[Dict]) -> None:
        if not games:
            return
        now = time.time()
        if now - self._last_advance < self.teams_game_duration:
            return
        self._last_advance = now
        self._index[mode] = (self._index.get(mode, 0) + 1) % len(games)

    # ------------------------------------------------------------------
    def get_cycle_duration(self, display_mode: str = None) -> Optional[float]:
        if self.teams_layout == "strip":
            teams = self.games.teams_with_games() if self.games else []
            if not teams or self.strip is None:
                return None
            pairs = [(t, self.games.headline_games(t)) for t in teams]
            panel_game, _ = self._panel_game()
            if panel_game is not None:
                pairs = [
                    (t, [g for g in games if g.get("id") != panel_game.get("id")])
                    for t, games in pairs
                ]
            pairs = [(t, g) for t, g in pairs
                     if g or (t.get("favorite_player") or "").strip()]
            any_live = self.games.has_any_live()
            boards, awards = self._leaderboards()
            countdown_events = self._countdowns()
            if any_live:
                boards, awards, countdown_events = [], [], []
            weather_show_forecast = not (
                any_live and self.teams_weather_hide_forecast_when_live
            )
            other_live = (
                self.games.other_live_games(
                    self.teams_other_live_limit,
                    followed_leagues_only=self.teams_other_live_followed_leagues_only,
                    per_league_limit=self.teams_other_live_per_league_limit,
                )
                if self.teams_other_live_on else []
            )
            if not pairs and not other_live and panel_game is None:
                return None
            built = self.strip.build_strip(
                pairs, leaderboards=boards, awards=awards,
                weather=self._weather_data,
                clock=datetime.now(),
                other_live=other_live,
                team_mvps=self._team_mvps(),
                countdowns=countdown_events,
                streaks=self._streaks(),
                weather_show_forecast=weather_show_forecast,
                show_clock=panel_game is not None,
                weather_show_current=panel_game is not None,
                rivalry_live_boost=self.teams_rivalry_live_boost)
            if built is None:
                return None
            speed = self._effective_scroll_speed(pairs, panel_game)
            span = self.strip.scroll_span(built)
            duration = span / speed
            return min(duration, self.teams_max_visit)

        mode = display_mode or self._current_mode
        games = self._games_for(mode) if mode else []
        if not games:
            return None
        return min(len(games) * self.teams_game_duration, self.teams_max_visit)

    def is_cycle_complete(self) -> bool:
        """Release the panel when the board has had its turn.

        The cap matters: without it a plugin holds the display until it has
        worked through its whole list, which starves everything else in the
        rotation.
        """
        if self.teams_layout == "strip":
            teams = self.games.teams_with_games() if self.games else []
            if not teams:
                return True
            # One complete pass of the strip is a cycle.
            if "pass" in self._seen.get(MODE_TEAMS, set()):
                self._release(MODE_TEAMS)
                self._scroll_offset = 0.0
                return True
            started = self._mode_started.get(MODE_TEAMS)
            if started and (time.time() - started) >= self.teams_max_visit:
                self._release(MODE_TEAMS)
                return True
            return False

        mode = self._current_mode
        if not mode:
            return True
        games = self._games_for(mode)
        if not games:
            return True
        if len(self._seen.get(mode, set())) >= len(games):
            self._release(mode)
            return True
        started = self._mode_started.get(mode)
        if started and (time.time() - started) >= self.teams_max_visit:
            self._release(mode)
            return True
        return False

    def _release(self, mode: str) -> None:
        self._seen.pop(mode, None)
        self._mode_started.pop(mode, None)

    def reset_cycle_state(self) -> None:
        if BasePlugin and hasattr(super(), "reset_cycle_state"):
            try:
                super().reset_cycle_state()
            except Exception:
                pass
        self._seen.clear()
        self._mode_started.clear()

    def has_live_priority(self) -> bool:
        """A followed team playing right now should interrupt the rotation."""
        return bool(self.games and self.games.has_live())

    def has_live_content(self) -> bool:
        return bool(self.games and self.games.has_live())

    # ------------------------------------------------------------------
    @staticmethod
    def _manifest_version() -> str:
        try:
            path = os.path.join(os.path.dirname(__file__), "manifest.json")
            with open(path, encoding="utf-8") as fh:
                return str(json.load(fh).get("version") or "0.0.0")
        except Exception:
            return "0.0.0"

    def get_info(self) -> Dict[str, Any]:
        try:
            counts = {}
            if self.games:
                for mode, state in MODE_STATE.items():
                    counts[mode] = len(self.games.games(state))
            return {
                "plugin_id": self.plugin_id,
                "name": "Local Scoreboard",
                "version": self._manifest_version(),
                "enabled": self.is_enabled,
                "display_size": f"{self.display_width}x{self.display_height}",
                "display_profile": (self.renderer.profile.describe()
                                    if self.renderer else "unavailable"),
                "fonts": (self.renderer.font_report()
                          if self.renderer else "unavailable"),
                "teams": [t.get("abbr") for t in self.teams_config],
                "games": counts,
                "live": bool(self.games and self.games.has_live()),
                "last_update": self._last_update,
            }
        except Exception as e:
            return {"plugin_id": self.plugin_id, "name": "Local Scoreboard", "error": str(e)}

    def cleanup(self) -> None:
        try:
            if self.games and getattr(self.games, "source", None):
                session = getattr(self.games.source, "session", None)
                if session:
                    session.close()
        except Exception as e:
            self.logger.debug("Cleanup error: %s", e)
        try:
            if self.strip:
                self.strip.close()
        except Exception as e:
            self.logger.debug("Cleanup error (strip worker): %s", e)
