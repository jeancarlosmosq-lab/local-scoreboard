"""
Baseball Leaders Manager

Handles fetching and caching of season statistical leaders. Modelled on the
scoreboard plugin's rankings_manager.py: fetch on a slow cycle, cache in
memory, serve from cache on the render path so drawing a frame never blocks
on the network.

Season leaders move slowly -- a player's home run total changes at most a few
times a day -- so the default refresh is 6 hours. That is roughly 4 HTTP calls
per day per stat group, versus the scoreboard's 60-second live polling.
"""

import logging
import time
from typing import Dict, List, Optional

from leaders_data_source import (
    MLBStatsLeadersSource,
    ascii_fold,
    HITTING_CATEGORIES,
    PITCHING_CATEGORIES,
    ALL_CATEGORIES,
    LEAGUE_SCOPES,
    POOL_QUALIFIED,
    POOL_ROOKIES,
)


class BaseballLeadersManager:
    """Manages season leaders fetching and caching."""

    def __init__(
        self,
        logger: logging.Logger,
        cache_manager=None,
        cache_duration: int = 21600,
        leaders_per_category: int = 5,
        fetch_player_stats: bool = True,
        partial_retry_interval: int = 600,
    ):
        """
        Args:
            logger: Logger instance
            cache_manager: LEDMatrix cache manager (optional). When present,
                leaders survive a plugin restart, so the display has content
                immediately instead of a blank screen until the first fetch.
            cache_duration: Seconds before a refetch. Default 6 hours.
            leaders_per_category: How many players to keep per category.
        """
        self.logger = logger
        self.cache_manager = cache_manager
        self.cache_duration = cache_duration
        self.leaders_per_category = leaders_per_category
        self.fetch_player_stats = fetch_player_stats
        # How soon to retry after an incomplete fetch. A partial result used
        # to be cached and stamped fresh like a good one, so a single bad
        # fetch left a category empty on screen for the full six hours.
        self.partial_retry_interval = partial_retry_interval

        try:
            self.data_source = MLBStatsLeadersSource(logger)
        except Exception as e:
            self.logger.warning(f"Failed to initialize MLBStatsLeadersSource: {e}")
            self.data_source = None

        # Keyed "group:scope" (e.g. "hitting:al") -> {category: [rows]}.
        # Scoping the cache key rather than nesting keeps every existing
        # lookup a single dict access on the render path.
        self._leaders_cache: Dict[str, Dict[str, List[Dict]]] = {}
        self._cache_timestamp: Dict[str, float] = {}

        # Full season stat lines, keyed "group:scope" -> {player_id: {LABEL: value}}.
        # Separate from the leaderboards because a player's whole line is not
        # derivable from the one category they ranked in.
        self._player_stats: Dict[str, Dict[str, Dict[str, str]]] = {}

        # Whole-team rosters, for a team MVP ranked against teammates
        # instead of the league -- a bigger, rarer fetch than the
        # leaderboards (every player on the roster, not just whoever
        # already leads a category), keyed by team abbreviation.
        self._roster_hitting: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._roster_pitching: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._roster_names: Dict[str, Dict[str, str]] = {}
        self._roster_full_names: Dict[str, Dict[str, str]] = {}
        self._roster_fetched_at: Dict[str, float] = {}

        self._restore_from_persistent_cache()

    # ------------------------------------------------------------------
    # Persistent Cache (Survives Restarts)
    # ------------------------------------------------------------------
    @staticmethod
    def key(group: str, scope: str = "mlb", pool: str = "") -> str:
        """Internal cache key for a (stat group, league scope, pool) triple.

        The pool suffix is omitted for the default qualified pool so existing
        keys are unchanged; only rookie boards get "…:rookie".
        """
        base = f"{group}:{scope}"
        return f"{base}:{pool}" if pool else base

    # Bumped whenever the shape or trimming of cached rows changes. Stale
    # entries written by an older version are then simply never found, rather
    # than being served for up to a full cache lifetime -- which is how a
    # fixed bug can appear to persist long after the fix is deployed.
    CACHE_SCHEMA = 2

    def _cache_key(self, group: str, scope: str = "mlb", pool: str = "") -> str:
        suffix = f"_{pool}" if pool else ""
        return f"baseball_stats_v{self.CACHE_SCHEMA}_leaders_{group}_{scope}{suffix}"

    def _restore_from_persistent_cache(self) -> None:
        """Warm the in-memory cache from disk so the first frame has data."""
        if not self.cache_manager:
            return
        for group in ("hitting", "pitching"):
            for scope in LEAGUE_SCOPES:
                key = self.key(group, scope)
                try:
                    cached = self.cache_manager.get(self._cache_key(group, scope))
                    if not (isinstance(cached, dict) and cached.get("leaders")):
                        continue

                    restored = cached["leaders"]
                    # A cache holding fewer players than we intend to show is
                    # treated as stale rather than displayed short. Otherwise
                    # a thin fetch keeps being served for hours after the
                    # conditions that caused it have passed.
                    thin = [
                        c for c, rows in restored.items()
                        if len(rows) < self.leaders_per_category
                    ]
                    if thin:
                        self.logger.info(
                            "Ignoring cached %s leaders: %s had fewer than %d players",
                            key, ", ".join(sorted(thin)), self.leaders_per_category,
                        )
                        continue

                    self._leaders_cache[key] = restored
                    self._cache_timestamp[key] = float(cached.get("timestamp", 0))
                    self.logger.debug(f"Restored {key} leaders from persistent cache")
                except Exception as e:
                    self.logger.debug(f"Could not restore {key} leaders from cache: {e}")

    def _write_persistent_cache(
        self, group: str, scope: str = "mlb", pool: str = ""
    ) -> None:
        if not self.cache_manager:
            return
        key = self.key(group, scope, pool)
        try:
            self.cache_manager.set(
                self._cache_key(group, scope, pool),
                {
                    "leaders": self._leaders_cache.get(key, {}),
                    "timestamp": self._cache_timestamp.get(key, time.time()),
                },
            )
        except Exception as e:
            self.logger.debug(f"Could not persist {key} leaders: {e}")

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------
    def _is_stale(self, group: str, scope: str = "mlb", pool: str = "") -> bool:
        key = self.key(group, scope, pool)
        if key not in self._leaders_cache:
            return True
        age = time.time() - self._cache_timestamp.get(key, 0)
        return age >= self.cache_duration

    def refresh(
        self,
        group: str,
        scope: str = "mlb",
        categories: Optional[List[str]] = None,
        pool: str = "",
    ) -> None:
        """Refetch one (stat group, league scope) pair if its cache expired.

        Safe to call every update tick -- it returns immediately when the
        cache is still warm, so this costs nothing on the hot path.
        """
        if not self.data_source:
            return
        if not self._is_stale(group, scope, pool):
            return

        if categories is None:
            categories = list(
                HITTING_CATEGORIES if group == "hitting" else PITCHING_CATEGORIES
            )

        key = self.key(group, scope, pool)
        player_pool = POOL_ROOKIES if pool == "rookie" else POOL_QUALIFIED

        # Ask for a deeper slice than we intend to show, then trim.
        # StatsAPI's limit interacts badly with ties -- categories like wins,
        # where a dozen pitchers sit on the same total, can come back with
        # fewer usable rows than requested. Over-fetching and trimming locally
        # guarantees a full board whenever the data exists at all.
        fetch_limit = max(self.leaders_per_category * 3, 10)

        try:
            leaders = self.data_source.fetch_leaders(
                categories=categories,
                stat_group=group,
                limit=fetch_limit,
                scope=scope,
                player_pool=player_pool,
            )

            # The ROOKIES pool value is undocumented. If it comes back empty,
            # fall back to filtering the ordinary leaderboards by debut date
            # rather than silently showing no Rookie of the Year at all.
            if pool == "rookie" and not leaders:
                leaders = self._rookie_fallback(group, scope, categories)
        except Exception as e:
            self.logger.error(f"Error refreshing {key} leaders: {e}")
            return

        if not leaders:
            # Keep serving whatever we already had rather than blanking the
            # screen on a transient API failure. The timestamp is deliberately
            # not advanced, so the next tick retries.
            self.logger.warning(f"No {key} leaders returned; keeping previous data")
            return

        # Trim to the configured depth, dropping anything unnamed -- a row
        # without a name renders as a blank line, which looks like a bug.
        leaders = {
            category: [r for r in rows if r.get("name")][: self.leaders_per_category]
            for category, rows in leaders.items()
        }
        # Fold names again here: a cache written by an earlier version may
        # still hold accented text that the panel font cannot render.
        for rows in leaders.values():
            for row in rows:
                row["name"] = ascii_fold(row.get("name", ""))
                row["short_name"] = ascii_fold(row.get("short_name", ""))
                row["team"] = ascii_fold(row.get("team", ""))
        leaders = {c: r for c, r in leaders.items() if r}
        if not leaders:
            self.logger.warning(f"{key}: every category was empty after trimming")
            return

        self._leaders_cache[key] = leaders

        # Only a complete result earns the full cache lifetime. Anything short
        # is stamped so it expires within partial_retry_interval instead, so
        # the display recovers on its own rather than showing a hole all day.
        expected = set(categories)
        got = {c for c, rows in leaders.items() if rows}
        incomplete = expected - got

        if incomplete:
            self._cache_timestamp[key] = (
                time.time() - self.cache_duration + self.partial_retry_interval
            )
            self.logger.warning(
                "%s: incomplete (missing %s); will retry in ~%ds",
                key, ", ".join(sorted(incomplete)), self.partial_retry_interval,
            )
        else:
            self._cache_timestamp[key] = time.time()

        self._write_persistent_cache(group, scope, pool)
        breakdown = ", ".join(
            f"{cat}={len(rows)}" for cat, rows in sorted(leaders.items())
        )
        self.logger.info(
            "Refreshed %s leaders: %d categories, %d rows (%s)",
            key, len(leaders), sum(len(v) for v in leaders.values()), breakdown,
        )
        # A category that comes back with fewer players than asked for is
        # worth flagging -- it looks identical on screen to a rendering bug.
        thin = [c for c, r in leaders.items() if len(r) < self.leaders_per_category]
        if thin:
            self.logger.warning(
                "%s: fewer players than requested (%d) in: %s",
                key, self.leaders_per_category, ", ".join(sorted(thin)),
            )

        if self.fetch_player_stats:
            self._refresh_player_stats(group, scope, pool)

    def _rookie_fallback(
        self, group: str, scope: str, categories: List[str]
    ) -> Dict[str, List[Dict]]:
        """Build rookie leaderboards by filtering ordinary ones on debut date.

        Fetches a deeper slice than usual (rookies are scattered through the
        standings, not clustered at the top) and keeps only players who
        debuted this season, re-ranking what survives.
        """
        try:
            wide = self.data_source.fetch_leaders(
                categories=categories,
                stat_group=group,
                limit=max(50, self.leaders_per_category * 10),
                scope=scope,
                player_pool=POOL_QUALIFIED,
            )
        except Exception as e:
            self.logger.error(f"Rookie fallback fetch failed for {group}/{scope}: {e}")
            return {}

        if not wide:
            return {}

        ids = []
        for rows in wide.values():
            for row in rows:
                pid = row.get("player_id")
                if pid and pid not in ids:
                    ids.append(pid)

        rookie_ids = self.data_source.fetch_rookie_ids(ids)
        if not rookie_ids:
            self.logger.info(f"No rookies found in {group}/{scope} leaderboards")
            return {}

        filtered: Dict[str, List[Dict]] = {}
        for category, rows in wide.items():
            kept = [r for r in rows if r.get("player_id") in rookie_ids]
            for i, row in enumerate(kept[: self.leaders_per_category], start=1):
                row = dict(row)
                row["rank"] = i  # re-rank among rookies, not all of MLB
                filtered.setdefault(category, []).append(row)

        self.logger.info(
            f"Rookie fallback built {len(filtered)} categories for {group}/{scope}"
        )
        return filtered

    def _refresh_player_stats(self, group: str, scope: str, pool: str = "") -> None:
        """Fetch full stat lines for everyone on this group's leaderboards.

        One extra HTTP call per (group, scope) on the same slow cache cycle --
        a rounding error next to what it buys, which is every player's real
        numbers rather than only the category they happened to rank in.
        """
        key = self.key(group, scope, pool)
        player_ids = []
        for rows in self._leaders_cache.get(key, {}).values():
            for row in rows:
                pid = row.get("player_id")
                if pid and pid not in player_ids:
                    player_ids.append(pid)

        if not player_ids:
            return

        try:
            stats = self.data_source.fetch_player_stats(player_ids, group)
        except Exception as e:
            self.logger.error(f"Error fetching player stats for {key}: {e}")
            return

        if stats:
            self._player_stats[key] = stats
            self.logger.info(
                f"Fetched stat lines for {len(stats)}/{len(player_ids)} "
                f"{key} players"
            )

    # --- Public helpers -------------------------------------------------
    # Not used by the plugin's own hot path (which refreshes per scope), but
    # kept as the obvious entry points for scripts, tests and future callers.

    def refresh_all(self, scopes: Optional[List[str]] = None) -> None:
        for scope in scopes or ["mlb"]:
            self.refresh("hitting", scope)
            self.refresh("pitching", scope)

    # ------------------------------------------------------------------
    # Reads (Render Path -- Cache Only, Never Fetches)
    # ------------------------------------------------------------------
    def get_category(
        self, category: str, scope: str = "mlb", pool: str = ""
    ) -> List[Dict]:
        """Return cached rows for one category, or [] if not loaded yet.

        Trimmed on the way out as well as on the way in. Lowering the
        configured depth would otherwise leave boards cached at the old depth
        still showing the old number of players, so some categories showed
        three and others five depending on when each was last fetched.
        """
        group = "hitting" if category in HITTING_CATEGORIES else "pitching"
        rows = self._leaders_cache.get(self.key(group, scope, pool), {}).get(
            category, []
        )
        return rows[: self.leaders_per_category]

    def get_group(
        self, group: str, scope: str = "mlb", pool: str = ""
    ) -> Dict[str, List[Dict]]:
        return self._leaders_cache.get(self.key(group, scope, pool), {})

    def available_categories(
        self, requested: List[str], scope: str = "mlb", pool: str = ""
    ) -> List[str]:
        """Filter a configured category list down to ones we actually have.

        This is what stops the display from showing an empty screen for a
        category the API did not return -- an empty category is skipped in
        rotation rather than rendered blank.
        """
        return [
            c for c in requested
            if c in ALL_CATEGORIES and self.get_category(c, scope, pool)
        ]

    def get_player_stats(
        self, player_id: str, group: str, scope: str = "mlb", pool: str = ""
    ) -> Dict[str, str]:
        """Return one player's full season line, or {} if not fetched."""
        if not player_id:
            return {}
        pid = str(player_id)
        # Fall back to the qualified-pool stats: a rookie who also appears on
        # the main leaderboards has the same season line either way, and this
        # avoids a blank stat row when only one pool has been fetched.
        for candidate_pool in ([pool, ""] if pool else [""]):
            found = self._player_stats.get(
                self.key(group, scope, candidate_pool), {}
            ).get(pid)
            if found:
                return found
        return {}

    def has_player_stats(
        self, group: str, scope: str = "mlb", pool: str = ""
    ) -> bool:
        return bool(
            self._player_stats.get(self.key(group, scope, pool))
            or self._player_stats.get(self.key(group, scope))
        )

    # ------------------------------------------------------------------
    # Whole-Team Rosters (For A Team MVP Ranked Against Teammates)
    # ------------------------------------------------------------------
    def refresh_team_roster(self, team_abbr: str) -> None:
        """Fetch one team's active roster and every player's season line.

        Never called from the render path -- this is a real HTTP request
        (the roster itself, then a stat-line request per stat group), on
        the same cache_duration as the leaderboards. The throttle stamp is
        only written after a successful fetch: stamping before the network
        call left a failed roster blank for the full cache_duration.
        """
        if self.data_source is None or not team_abbr:
            return
        now = time.time()
        if now - self._roster_fetched_at.get(team_abbr, 0.0) < self.cache_duration:
            return

        try:
            roster = self.data_source.fetch_team_roster(team_abbr)
        except Exception as e:
            self.logger.debug(f"Roster fetch failed for {team_abbr}: {e}")
            return
        if not roster:
            # Empty is a real answer (unknown abbr, off-season) -- throttle.
            self._roster_fetched_at[team_abbr] = now
            return

        player_ids = [p["player_id"] for p in roster]
        names = {p["player_id"]: p["short_name"] for p in roster}
        full_names = {p["player_id"]: p["name"] for p in roster}
        try:
            hitting = self.data_source.fetch_player_stats(player_ids, "hitting")
            pitching = self.data_source.fetch_player_stats(player_ids, "pitching")
        except Exception as e:
            self.logger.debug(f"Roster stats failed for {team_abbr}: {e}")
            return

        self._roster_hitting[team_abbr] = hitting
        self._roster_pitching[team_abbr] = pitching
        self._roster_names[team_abbr] = names
        self._roster_full_names[team_abbr] = full_names
        self._roster_fetched_at[team_abbr] = now
        self.logger.info(
            f"Refreshed {team_abbr} roster: {len(roster)} players, "
            f"{len(hitting)} with hitting stats, {len(pitching)} with "
            f"pitching stats"
        )

    def get_team_roster(self, team_abbr: str):
        """(hitting_stats, pitching_stats, short_names, full_names), all
        keyed by player_id -- empty dicts if the roster has not been
        fetched yet."""
        return (
            self._roster_hitting.get(team_abbr, {}),
            self._roster_pitching.get(team_abbr, {}),
            self._roster_names.get(team_abbr, {}),
            self._roster_full_names.get(team_abbr, {}),
        )

    def teams_in_cache(self, scope: str = "mlb") -> List[str]:
        """Every team abbreviation currently on a leaderboard.

        Used to prefetch logos off the render path, so the first frame after
        a refresh is not the one paying for the downloads.
        """
        seen = []
        for group in ("hitting", "pitching"):
            for rows in self._leaders_cache.get(self.key(group, scope), {}).values():
                for row in rows:
                    team = row.get("team")
                    if team and team not in seen:
                        seen.append(team)
        return seen

    def has_data(self, scope: Optional[str] = None) -> bool:
        if scope is None:
            return any(self._leaders_cache.values())
        return any(
            self._leaders_cache.get(self.key(g, scope))
            for g in ("hitting", "pitching")
        )

    def clear_cache(self, group: Optional[str] = None, scope: str = "mlb") -> None:
        if group:
            key = self.key(group, scope)
            self._leaders_cache.pop(key, None)
            self._cache_timestamp.pop(key, None)
        else:
            self._leaders_cache.clear()
            self._cache_timestamp.clear()
