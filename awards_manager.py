"""
Baseball Awards Manager

Computes award "watch lists" from the leaderboard data already in memory. No
extra network calls -- this is pure arithmetic over what leaders_manager.py
has cached.

Scoring method (Borda count):
    For each category in an award's set, a player ranked r out of N receives
    (N - r + 1) points. Points are summed across categories, then divided by
    the theoretical maximum to give a 0-100 score.

Why Borda rather than something cleverer: a player who appears near the top of
*several* categories outscores one who leads a single category, which is a
reasonable first approximation of how MVP voting actually behaves. It needs no
training data, no external model, and no odds feed -- and it is transparent
enough that you can look at the screen and understand why a name is there.

What this is NOT: a prediction. It has no concept of defensive value, park
factors, position scarcity, WAR, playoff impact, or narrative -- all of which
move real voting. Treat the output as "who is having the loudest statistical
season", and label it that way on screen.
"""

import logging
from typing import Dict, List, Optional

from leaders_data_source import (
    ALL_CATEGORIES,
    HITTING_CATEGORIES,
    PITCHING_CATEGORIES,
)


# Which categories feed which award. Weights let a category count for more
# than one Borda point per rank -- home runs and RBI drive MVP narratives
# harder than stolen bases do, and ERA carries Cy Young voting more than wins.
# How much each additional category a player ranks in lifts their score.
# Small on purpose -- it exists to separate otherwise-tied group leaders, not
# to let a broad-but-mediocre season outrank a dominant narrow one.
BREADTH_BONUS = 0.12


AWARD_DEFINITIONS = {
    # "WATCH" used to be on every entry's own label too, on top of the
    # section banner already reading "AWARDS WATCH" -- doubling the framing
    # for something the banner already said once for the whole section.
    "mvp": {
        "label": "MVP",
        "group": "hitting",
        "groups": ["hitting"],
        "pool": "",
        "categories": {
            "homeRuns": 1.2,
            "runsBattedIn": 1.1,
            "hits": 1.0,
            "runs": 1.0,
            "stolenBases": 0.6,
        },
    },
    "cy_young": {
        "label": "CY YOUNG",
        "group": "pitching",
        "groups": ["pitching"],
        "pool": "",
        "categories": {
            "earnedRunAverage": 1.3,
            "strikeouts": 1.1,
            "wins": 0.9,
        },
    },
    # Rookie of the Year can go to a hitter or a pitcher, so it scores both
    # groups and merges them. Each group is normalised against its own leader
    # before merging -- otherwise whichever group happened to have more
    # categories would dominate purely on point volume.
    "roy": {
        "label": "ROOKIE OF YR",
        "group": "hitting",
        "groups": ["hitting", "pitching"],
        "pool": "rookie",
        "categories": {
            "homeRuns": 1.1,
            "battingAverage": 1.1,
            "runsBattedIn": 1.0,
            "hits": 1.0,
            "runs": 0.9,
            "stolenBases": 0.6,
            "earnedRunAverage": 1.2,
            "strikeouts": 1.0,
            "wins": 0.8,
        },
    },
    # The real Triple Crown is leading all three outright, but a strip
    # showing five names under "TRIPLE CROWN" already reads as "in the
    # running", not "has won it" -- the same computed-ranking framing MVP
    # and Cy Young already use. Equal weight on purpose: the three crown
    # categories are peers by definition, not weighted toward one.
    "triple_crown": {
        "label": "TRIPLE CROWN",
        "group": "hitting",
        "groups": ["hitting"],
        "pool": "",
        "categories": {
            "battingAverage": 1.0,
            "homeRuns": 1.0,
            "runsBattedIn": 1.0,
        },
    },
}


class BaseballAwardsManager:
    """Derives award watch lists from cached leaderboard data."""

    def __init__(self, logger: logging.Logger, leaders_manager, top_n: int = 5):
        """
        Args:
            logger: Logger instance
            leaders_manager: a BaseballLeadersManager to read cached rows from
            top_n: how many candidates to keep per award
        """
        self.logger = logger
        self.leaders_manager = leaders_manager
        self.top_n = top_n

    def compute(self, award_key: str, scope: str = "mlb") -> List[Dict]:
        """Compute one award's watch list for a league scope.

        MVP and Cy Young are per-league awards in reality, so scope="al" or
        "nl" produces the more meaningful list; "mlb" ranks both leagues
        together.

        Returns a list of {rank, name, short_name, team, score, group,
        categories} sorted best-first, or [] if there is not enough data yet.
        """
        definition = AWARD_DEFINITIONS.get(award_key)
        if not definition:
            self.logger.warning(f"Unknown award key: {award_key}")
            return []

        pool = definition.get("pool", "")
        groups = definition.get("groups") or [definition.get("group", "hitting")]

        # Score each stat group independently, then merge. Hitters and
        # pitchers share no categories, so a single combined Borda count
        # would rank them against a scale neither of them competes on.
        merged: List[Dict] = []
        for group in groups:
            scored = self._score_group(definition, group, scope, pool)
            merged.extend(scored)

        if not merged:
            return []

        # Normalising each group against its own leader means the best hitter
        # and the best pitcher would both land on exactly 1.0 and tie for
        # first. Breadth breaks that: a player who ranks in five categories
        # has a stronger case than one who ranks in two, which is also how
        # the actual voting tends to go.
        for entry in merged:
            breadth = len(entry.get("categories", []))
            entry["_final"] = entry["_normalised"] * (1.0 + BREADTH_BONUS * (breadth - 1))

        merged.sort(key=lambda e: e["_final"], reverse=True)

        merged = [e for e in merged if e.get("name")]
        if not merged:
            return []

        results = []
        for i, entry in enumerate(merged[: self.top_n], start=1):
            results.append(
                {
                    "rank": i,
                    "player_id": entry.get("player_id", ""),
                    "name": entry["name"],
                    "short_name": entry["short_name"],
                    "team": entry.get("team", ""),
                    "group": entry["group"],
                    "score": round(100.0 * entry["_final"] / merged[0]["_final"], 1),
                    "categories": sorted(entry["categories"], key=lambda c: c["rank"]),
                }
            )

        return results

    def _score_group(
        self, definition: Dict, group: str, scope: str, pool: str
    ) -> List[Dict]:
        """Borda-score one stat group's contribution to an award.

        Returns entries carrying a "_normalised" score in 0..1, relative to
        that group's own leader, so groups can be compared to each other.
        """
        group_categories = (
            HITTING_CATEGORIES if group == "hitting" else PITCHING_CATEGORIES
        )
        applicable = {
            cat: weight
            for cat, weight in definition["categories"].items()
            if cat in group_categories
        }
        if not applicable:
            return []

        scores: Dict[str, Dict] = {}

        for category, weight in applicable.items():
            rows = self.leaders_manager.get_category(category, scope, pool)
            if not rows:
                # Category missing from the API response this cycle. Skip it
                # rather than treating every player as tied last, which would
                # distort the whole board.
                continue

            n = len(rows)
            for row in rows:
                if not row.get("name"):
                    # Without a name there is nothing to draw, and a blank
                    # row on an award board reads as a rendering fault.
                    continue
                player_id = row.get("player_id") or row.get("name")
                if not player_id:
                    continue

                points = weight * (n - row["rank"] + 1)

                entry = scores.setdefault(
                    player_id,
                    {
                        "player_id": row.get("player_id", ""),
                        "name": row["name"],
                        "short_name": row.get("short_name", row["name"]),
                        "team": row.get("team", ""),
                        "group": group,
                        "points": 0.0,
                        "categories": [],
                    },
                )
                entry["points"] += points
                entry["categories"].append(
                    {
                        "label": ALL_CATEGORIES.get(category, {}).get("label", category),
                        "rank": row["rank"],
                        "value": row.get("value", ""),
                    }
                )

        if not scores:
            return []

        ranked = sorted(scores.values(), key=lambda e: e["points"], reverse=True)
        best = ranked[0]["points"] or 1.0
        for entry in ranked:
            entry["_normalised"] = entry["points"] / best
        return ranked

    def available_awards(self, requested: List[str], scope: str = "mlb") -> List[str]:
        """Filter configured awards down to those with computable data."""
        return [
            a for a in requested
            if a in AWARD_DEFINITIONS and self.compute(a, scope)
        ]

    def team_best(self, team_abbr: str, scope: str = "mlb") -> Optional[Dict]:
        """One followed team's own standout this season, from the same
        leaderboard data already on the board -- not a new fetch, and not a
        guess at who "should" be having a good year.

        Scored the same way MVP (hitting) and Cy Young (pitching) already
        are, merged, since a team's own best season could be either kind.
        The real limitation: this only ever sees players who already rank
        in a fetched category. A quietly good player who never cracks the
        top of any single stat will not appear here -- the same honest
        limit the league-wide award lists already carry, just applied to
        one team instead of the whole league.
        """
        definition = {
            "categories": {
                **AWARD_DEFINITIONS["mvp"]["categories"],
                **AWARD_DEFINITIONS["cy_young"]["categories"],
            }
        }
        merged: List[Dict] = []
        for group in ("hitting", "pitching"):
            merged.extend(self._score_group(definition, group, scope, ""))
        if not merged:
            return None

        wanted = (team_abbr or "").upper()
        candidates = [e for e in merged if (e.get("team") or "").upper() == wanted]
        if not candidates:
            return None

        for entry in candidates:
            breadth = len(entry.get("categories", []))
            entry["_final"] = entry["_normalised"] * (1.0 + BREADTH_BONUS * (breadth - 1))
        candidates.sort(key=lambda e: e["_final"], reverse=True)

        best = candidates[0]
        return {
            "player_id": best.get("player_id", ""),
            "name": best["name"],
            "short_name": best["short_name"],
            "team": wanted,
            "group": best["group"],
            "categories": sorted(best["categories"], key=lambda c: c["rank"]),
        }

    def team_mvp_from_roster(
        self,
        hitting_stats: Dict[str, Dict[str, str]],
        pitching_stats: Dict[str, Dict[str, str]],
        short_names: Dict[str, str],
        full_names: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict]:
        """A team's own best player this season, ranked against nobody but
        their own teammates.

        team_best() only ever sees a player who already ranks in a
        league-wide leaderboard's top N -- most followed teams have nobody
        there most of the time, which left most teams with no MVP entry at
        all. This computes its own Borda ranks directly from the roster's
        raw stat lines (already fetched for the full stat line every row
        shows), scored within the roster instead of the whole league, so
        every team with any stats at all gets a real answer instead of an
        absence.

        The same weighted categories as the league-wide MVP (hitting) and
        Cy Young (pitching) decide what counts, merged the same way --
        each group normalised against its own best on this specific
        roster before comparing hitters to pitchers.
        """

        def score_group(stats_by_player, categories_source, weights):
            scores: Dict[str, Dict] = {}
            for cat, weight in weights.items():
                info = categories_source.get(cat) or {}
                label = info.get("label")
                if not label:
                    continue
                higher_is_better = info.get("higher_is_better", True)

                entries = []
                for pid, stats in stats_by_player.items():
                    raw = stats.get(label)
                    if raw is None:
                        continue
                    try:
                        entries.append((pid, float(raw)))
                    except (TypeError, ValueError):
                        continue
                if not entries:
                    continue

                entries.sort(key=lambda e: e[1], reverse=higher_is_better)
                n = len(entries)
                for rank0, (pid, _val) in enumerate(entries):
                    points = weight * (n - rank0)
                    entry = scores.setdefault(
                        pid, {"points": 0.0, "categories": []})
                    entry["points"] += points
                    entry["categories"].append(label)

            if not scores:
                return []
            best_points = max(e["points"] for e in scores.values()) or 1.0
            return [
                {"player_id": pid, "_normalised": e["points"] / best_points,
                 "categories": e["categories"]}
                for pid, e in scores.items()
            ]

        merged = [
            {"group": "hitting", **entry}
            for entry in score_group(
                hitting_stats, HITTING_CATEGORIES,
                AWARD_DEFINITIONS["mvp"]["categories"])
        ] + [
            {"group": "pitching", **entry}
            for entry in score_group(
                pitching_stats, PITCHING_CATEGORIES,
                AWARD_DEFINITIONS["cy_young"]["categories"])
        ]
        if not merged:
            return None

        for entry in merged:
            breadth = len(entry.get("categories", []))
            entry["_final"] = entry["_normalised"] * (1.0 + BREADTH_BONUS * (breadth - 1))
        merged.sort(key=lambda e: e["_final"], reverse=True)

        best = merged[0]
        pid = best["player_id"]
        return {
            "player_id": pid,
            "name": (full_names or {}).get(pid, ""),
            "short_name": short_names.get(pid, ""),
            "group": best["group"],
        }
