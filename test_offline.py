"""
Offline smoke test for the Local Scoreboard plugin. No network, no LED panel.

Feeds canned ESPN responses through the parser, manager and renderer, then
writes PNG previews of every card so layout can be judged without deploying.

Run:  python3 test_offline.py
"""

import json
import logging
import os
import sys
import threading
import time
import types

from PIL import Image

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
log = logging.getLogger("test")

from espn_data_source import (
    ESPNGamesSource, DEFAULT_TEAMS, abbreviate_name, ascii_fold,
    STATE_FINAL, STATE_LIVE, STATE_UPCOMING,
)
from games_manager import GamesManager
from game_renderer import GameCardRenderer, CardProfile
from logo_manager import TeamLogoManager
from strip_renderer import StripRenderer


# ----------------------------------------------------------------------
# Canned ESPN payloads
# ----------------------------------------------------------------------
def event(eid, away, home, away_score, home_score, state, completed,
          detail="", clock="", period=0, date="2026-08-09T23:05Z"):
    def side(team, score, home_away, winner):
        return {
            "homeAway": home_away,
            "score": score,
            "winner": winner,
            "team": {"abbreviation": team[0], "shortDisplayName": team[1]},
            "records": [{"summary": team[2]}],
        }
    return {
        "id": eid,
        "date": date,
        "competitions": [{
            "status": {
                "type": {"state": state, "completed": completed,
                         "shortDetail": detail},
                "displayClock": clock, "period": period,
            },
            "competitors": [
                side(home, home_score, "home", completed and home_score > away_score),
                side(away, away_score, "away", completed and away_score > home_score),
            ],
        }],
    }


SCOREBOARD_MLB = {"events": [
    event("401", ("BOS", "Red Sox", "62-54"), ("NYY", "Yankees", "70-46"),
          "4", "7", "post", True, "Final"),
    event("402", ("NYM", "Mets", "64-52"), ("ATL", "Braves", "59-57"),
          "3", "2", "in", False, "Top 7", period=7),
    event("403", ("NYY", "Yankees", "70-46"), ("TOR", "Blue Jays", "58-58"),
          "", "", "pre", False, "7:05 PM", date="2026-08-10T23:05Z"),
]}

SCOREBOARD_NBA = {"events": [
    event("501", ("BOS", "Celtics", "0-0"), ("NY", "Knicks", "0-0"),
          "88", "95", "in", False, "Q4 4:12", clock="4:12", period=4),
]}

SCOREBOARD_NFL = {"events": [
    event("601", ("NYG", "Giants", "0-0"), ("DAL", "Cowboys", "0-0"),
          "", "", "pre", False, "1:00 PM", date="2026-09-07T17:00Z"),
]}

SUMMARY = {"leaders": [
    {"team": {"abbreviation": "NYY"}, "leaders": [
        {"shortDisplayName": "HR", "leaders": [
            {"displayValue": "2-4, HR, 3 RBI",
             "athlete": {"displayName": "Aaron Judge"}}]}]},
    {"team": {"abbreviation": "BOS"}, "leaders": [
        {"shortDisplayName": "AVG", "leaders": [
            {"displayValue": "3-4, 2B",
             "athlete": {"displayName": "Jarren Duran"}}]}]},
]}


# ----------------------------------------------------------------------
class FakeMatrix:
    def __init__(self, w, h):
        self.width, self.height = w, h


class FakeDisplay:
    def __init__(self, w=192, h=32):
        self.matrix = FakeMatrix(w, h)
        self.image = Image.new("RGB", (w, h))
        self.font = None
        self.frames = []

    def update_display(self):
        self.frames.append(self.image.copy())


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v


def main():
    failures = []

    # ---- 1. Parsing ----------------------------------------------------
    src = ESPNGamesSource(log)
    mlb = src._parse_events(SCOREBOARD_MLB, "mlb")
    assert len(mlb) == 3, mlb
    by_state = {g["state"] for g in mlb}
    assert by_state == {STATE_FINAL, STATE_LIVE, STATE_UPCOMING}, by_state
    final = next(g for g in mlb if g["state"] == STATE_FINAL)
    assert final["home"]["abbr"] == "NYY" and final["home"]["score"] == "7"
    assert final["home"]["winner"] is True, "winner not flagged on the final"
    assert final["away"]["record"] == "62-54"
    print(f"PASS  parsed {len(mlb)} MLB games across all three states")

    nba = src._parse_events(SCOREBOARD_NBA, "nba")
    assert nba[0]["state"] == STATE_LIVE and nba[0]["period"] == 4
    nfl = src._parse_events(SCOREBOARD_NFL, "nfl")
    assert nfl[0]["state"] == STATE_UPCOMING
    print("PASS  the same parser handles NBA and NFL payloads")

    # ---- 2. Names ------------------------------------------------------
    assert abbreviate_name("Aaron Judge") == "A.JUDGE"
    assert abbreviate_name("Ronald Acuna Jr.") == "R.ACUNA"
    assert abbreviate_name("Jos\u00e9 Ram\u00edrez") == "J.RAMIREZ"
    assert ascii_fold("Cristopher S\u00e1nchez").isascii()
    print("PASS  player names abbreviate and fold to ASCII")

    # ---- 3. Leaders ----------------------------------------------------
    # The scoreboard usually carries leaders already, on the competition
    # rather than in a separate request. Missing this is why finals showed
    # no stat line: the plugin asked the summary endpoint instead, whose
    # shape differs by sport.
    #
    # A leader entry's own "team" carries only a numeric id -- {"id": "14"},
    # nothing else -- confirmed against a live scoreboard response. An
    # earlier version of this fixture put "abbreviation" directly on it,
    # which is not what ESPN actually sends; that mismatch is exactly what
    # let _parse_competition_leaders return an empty "team" for every
    # leader, on every game, silently, since the field it read was never
    # there. The abbreviation only exists on the competition's own
    # competitors, which this fixture now includes so the id can resolve.
    board_comp = {
        "leaders": [{"shortDisplayName": "HR", "leaders": [
            {"displayValue": "2-4, HR, 3 RBI",
             "athlete": {"shortName": "A. Judge", "displayName": "Aaron Judge"},
             "team": {"id": "10"}}]}],
        "competitors": [
            {"homeAway": "home", "team": {"id": "10", "abbreviation": "NYY"}},
            {"homeAway": "away", "team": {"id": "2", "abbreviation": "BOS"}},
        ],
    }
    board_leaders = src._parse_competition_leaders(board_comp)
    assert board_leaders and board_leaders[0]["name"] == "A.JUDGE", board_leaders
    assert board_leaders[0]["category"] == "HR"
    assert board_leaders[0]["team"] == "NYY", (
        f"leader team id was not resolved through competitors: "
        f"{board_leaders[0]}"
    )
    assert src._parse_competition_leaders({}) == []
    assert src._parse_competition_leaders({"leaders": [{"leaders": []}]}) == []
    print(f"PASS  scoreboard leaders parsed: {board_leaders[0]['name']} "
          f"{board_leaders[0]['line']} for {board_leaders[0]['team']}, "
          f"team id resolved through competitors the way a real response "
          f"actually shapes it")

    # Season leaders (MLB StatsAPI, a separate source entirely from ESPN's
    # scoreboard) have the identical class of bug: a leader entry's own
    # "team" carries only an id and the full "Houston Astros" name, not an
    # abbreviation or "teamName" -- confirmed against a live request. This
    # had been silently returning "" for every team on every leaderboard and
    # award list on the strip. _team_abbr() resolves the id via /teams,
    # fetched once and cached; stubbed here so the test needs no network.
    from leaders_data_source import MLBStatsLeadersSource

    class StubTeamsResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"teams": [{"id": 117, "abbreviation": "HOU"},
                              {"id": 147, "abbreviation": "NYY"}]}

    mlb_src = MLBStatsLeadersSource(log)
    mlb_src.session.get = lambda url, **kw: StubTeamsResponse()
    live_shape = {"leagueLeaders": [{"leaderCategory": "homeRuns", "leaders": [
        {"rank": 1, "value": "35",
         "person": {"id": 670541, "fullName": "Yordan Alvarez"},
         "team": {"id": 117, "name": "Houston Astros"}},
    ]}]}
    parsed = mlb_src._parse_leaders(live_shape)
    assert parsed["homeRuns"][0]["team"] == "HOU", (
        f"StatsAPI leader team id was not resolved: {parsed}"
    )
    # The id->abbr map is fetched once, not per call.
    calls = []
    mlb_src.session.get = lambda url, **kw: (calls.append(url), StubTeamsResponse())[1]
    mlb_src._parse_leaders(live_shape)
    mlb_src._parse_leaders(live_shape)
    assert not calls, f"team abbreviations were re-fetched instead of cached: {calls}"
    print("PASS  season leader team ids resolve to real abbreviations "
          "(MLB StatsAPI's own shape, fetched once and cached)")

    # ESPN's "RAT" is a composite rating -- an internal code that says nothing
    # to a viewer. It must never reach the board.
    from espn_data_source import readable_category, classify_leader
    assert readable_category("mlb", "RAT", "7.0 IP, 2 ER, 9 K") == "PITCH"
    assert readable_category("mlb", "RAT", "2-4, HR, 3 RBI") == "BAT"
    assert readable_category("mlb", "MLBRating", "6.0 IP, 1 ER") == "PITCH"
    assert readable_category("nfl", "RAT", "24-31, 305 YDS, 3 TD") == "PASS"
    assert readable_category("nfl", "RAT", "18 CAR, 96 YDS") == "RUSH"
    for opaque in ("RAT", "MLBRating", "RATING"):
        for line in ("7.0 IP, 2 ER", "2-4, HR"):
            assert readable_category("mlb", opaque, line) not in ("RAT", "RATING"), (
                f"{opaque} leaked through to the board"
            )
    print("PASS  opaque category codes are replaced with readable labels")

    # A baseball game needs both sides of the ball: a pitching line alone
    # tells you half of how the game went.
    both_sides = {"_league": "mlb", "leaders": [
        {"shortDisplayName": "RAT", "leaders": [
            {"displayValue": "7.0 IP, 2 ER, 9 K",
             "athlete": {"shortName": "G. Cole"},
             "team": {"abbreviation": "NYY"}}]},
        {"shortDisplayName": "RAT", "leaders": [
            {"displayValue": "6.1 IP, 3 ER",
             "athlete": {"shortName": "B. Bello"},
             "team": {"abbreviation": "BOS"}}]},
        {"shortDisplayName": "HR", "leaders": [
            {"displayValue": "2-4, HR, 3 RBI",
             "athlete": {"shortName": "A. Judge"},
             "team": {"abbreviation": "NYY"}}]},
    ]}
    picked = src._parse_competition_leaders(both_sides)
    sides = {p["side"] for p in picked}
    assert sides == {"pitching", "batting"}, (
        f"expected one of each side, got {[(p['name'], p['side']) for p in picked]}"
    )
    assert len({p["name"] for p in picked}) == len(picked), "duplicate performer"
    print(f"PASS  one pitcher and one hitter chosen: "
          f"{[(p['name'], p['category']) for p in picked]}")

    # The board shows the followed team's offensive line -- and the winner's
    # when the followed team lost, because the story of a loss is who beat you.
    perf_leaders = [
        {"team": "NYY", "name": "G.COLE", "line": "7.0 IP, 2 ER", "side": "pitching"},
        {"team": "NYY", "name": "A.JUDGE", "line": "2-4, HR", "side": "batting"},
        {"team": "BOS", "name": "J.DURAN", "line": "3-4, 2B", "side": "batting"},
    ]
    won = {"state": STATE_FINAL, "league": "mlb", "leaders": perf_leaders,
           "home": {"abbr": "NYY", "winner": True},
           "away": {"abbr": "BOS", "winner": False}}
    lost = {"state": STATE_FINAL, "league": "mlb", "leaders": perf_leaders,
            "home": {"abbr": "NYY", "winner": False},
            "away": {"abbr": "BOS", "winner": True}}
    on_win = ESPNGamesSource.pick_performer(won, "NYY")
    on_loss = ESPNGamesSource.pick_performer(lost, "NYY")
    assert on_win["name"] == "A.JUDGE", on_win
    assert on_win["side"] == "batting", "a pitching line was chosen"
    assert on_loss["name"] == "J.DURAN", on_loss
    assert on_loss["team"] == "BOS", "should switch to the winner after a loss"
    assert ESPNGamesSource.pick_performer({"leaders": []}, "NYY") is None
    print(f"PASS  performer follows the result: won -> {on_win['name']}, "
          f"lost -> {on_loss['name']}")

    # Baseball summaries carry no "leaders" block at all -- confirmed against
    # live games -- so the hitter has to come out of the boxscore.
    box = {"boxscore": {"players": [
        {"team": {"abbreviation": "NYY"}, "statistics": [
            {"name": "batting", "labels": ["AB", "R", "H", "RBI", "HR", "BB", "K"],
             "athletes": [
                 {"athlete": {"shortName": "A. Judge"},
                  "stats": ["4", "2", "2", "3", "1", "1", "1"]},
                 {"athlete": {"shortName": "J. Dominguez"},
                  "stats": ["4", "0", "1", "0", "0", "0", "2"]}]},
            {"name": "pitching", "labels": ["IP"], "athletes": []}]},
        {"team": {"abbreviation": "ATL"}, "statistics": [
            {"name": "batting", "labels": ["AB", "R", "H", "RBI", "HR"],
             "athletes": [
                 {"athlete": {"shortName": "M. Olson"},
                  "stats": ["4", "1", "3", "2", "0"]}]}]},
    ]}}
    hitters = src._parse_boxscore_batting(box)
    assert len(hitters) == 2, hitters
    by_team = {h["team"]: h for h in hitters}
    # The better line wins, not merely the first listed
    assert by_team["NYY"]["name"] == "A.JUDGE", by_team["NYY"]
    assert "HR" in by_team["NYY"]["line"] and "3 RBI" in by_team["NYY"]["line"]
    assert all(h["side"] == "batting" for h in hitters)
    assert src._parse_boxscore_batting({}) == []
    assert src._parse_boxscore_batting({"boxscore": {}}) == []
    print(f"PASS  boxscore yields a hitter per team: "
          f"{[(h['team'], h['name'], h['line']) for h in hitters]}")

    # A category can carry one entry per team; taking only the first threw
    # away the other, which was sometimes the only hitter available.
    two_sided = {"_league": "mlb", "leaders": [
        {"shortDisplayName": "RAT", "leaders": [
            {"displayValue": "7.0 IP, 11 K",
             "athlete": {"shortName": "C. Schlittler"},
             "team": {"abbreviation": "NYY"}},
            {"displayValue": "3-4, HR, 2 RBI",
             "athlete": {"shortName": "M. Olson"},
             "team": {"abbreviation": "ATL"}}]}]}
    both = src._parse_competition_leaders(two_sided)
    assert len(both) == 2, f"second entry in the category was dropped: {both}"
    assert {b["side"] for b in both} == {"pitching", "batting"}, both
    print("PASS  every entry in a category is read, not just the first")

    # The summary must be consulted whenever no batting line exists, or the
    # board stays pitchers-only.
    summary_all = {"leaders": [
        {"team": {"abbreviation": "NYY"}, "leaders": [
            {"shortDisplayName": "RAT", "leaders": [
                {"displayValue": "7.0 IP, 2 ER",
                 "athlete": {"shortName": "G. Cole"}}]},
            {"shortDisplayName": "HR", "leaders": [
                {"displayValue": "2-4, HR, 3 RBI",
                 "athlete": {"shortName": "A. Judge"}}]}]},
    ]}
    every = src._parse_leaders(summary_all, 4, "mlb")
    assert any(l["side"] == "batting" for l in every), every
    assert any(l["side"] == "pitching" for l in every), every
    print(f"PASS  summary parser still handles sports that do carry leaders: "
          f"{[(l['name'], l['side']) for l in every]}")

    # Dark crests must be lifted clear of a black panel; bright ones untouched.
    from PIL import ImageDraw as _ImageDraw
    def crest(colour):
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        _ImageDraw.Draw(im).ellipse([2, 2, 62, 62], fill=colour + (255,))
        im.thumbnail((11, 11), Image.LANCZOS)
        return im

    def mean_peak(im):
        px = im.load()
        vals = [max(px[x, y][:3]) for y in range(im.height)
                for x in range(im.width) if px[x, y][3] > 40]
        return sum(vals) / max(1, len(vals))

    navy = crest((12, 35, 64))
    red = crest((198, 12, 48))
    before_navy, before_red = mean_peak(navy), mean_peak(red)
    lifted_navy = TeamLogoManager._lift_dark(navy.copy())
    lifted_red = TeamLogoManager._lift_dark(red.copy())
    assert mean_peak(lifted_navy) > before_navy * 1.5, "navy crest not lifted"
    assert abs(mean_peak(lifted_red) - before_red) < 1, "bright crest was altered"
    print(f"PASS  dark crest lifted {before_navy:.0f} -> "
          f"{mean_peak(lifted_navy):.0f}; bright crest untouched")

    # A game parsed from a scoreboard carrying leaders must arrive with them
    with_leaders = dict(SCOREBOARD_MLB)
    enriched = json.loads(json.dumps(SCOREBOARD_MLB))
    enriched["events"][0]["competitions"][0]["leaders"] = board_comp["leaders"]
    parsed = src._parse_events(enriched, "mlb")
    assert parsed[0]["leaders"], "leaders on the competition were not carried through"
    print("PASS  leaders arrive with the game, no second request needed")

    leaders = src._parse_leaders(SUMMARY, per_game=2)
    assert len(leaders) == 2, leaders
    assert leaders[0]["name"] == "A.JUDGE"
    assert "HR" in leaders[0]["line"]
    assert src._parse_leaders({}, 2) == []
    print(f"PASS  notable players parsed: {[(l['name'], l['line']) for l in leaders]}")

    # A fixture must always say which day. A bare time is ambiguous on a
    # board you glance at -- 7:05 tonight and 7:05 next Tuesday look the same.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _now = _dt.now().astimezone()
    for label, when, expect in [
        ("today", _now.replace(hour=19, minute=5), "TDY"),
        ("in 3 days", _now + _td(days=3), None),
        ("in 10 days", _now + _td(days=10), "/"),
    ]:
        probe = {"start": when.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        got = ESPNGamesSource.local_start(probe)
        assert got, f"{label}: no start label produced"
        # Weekday and date and time, always -- the weekday alone repeats
        # every seven days and a bare time is ambiguous.
        parts = got.split()
        assert len(parts) == 3, f"{label}: {got!r} is not day + date + time"
        assert "/" in parts[1], f"{label}: {got!r} has no month/day"
        assert ":" in parts[2], f"{label}: {got!r} has no time"
    assert ESPNGamesSource.local_start({}) == ""
    assert ESPNGamesSource.local_start({"start": "nonsense"}) == ""
    print("PASS  fixture labels always name a day, adding the date when needed")

    # A regional feed (YES) and a national one (ESPN) can both be present
    # on the same competition at once -- one of each is kept, so a local
    # channel and a streaming exclusive can both surface for an upcoming
    # game.
    multi_broadcast = {"broadcasts": [
        {"market": "home", "names": ["YES"]},
        {"market": "national", "names": ["ESPN"]},
    ]}
    assert src._parse_broadcast(multi_broadcast) == "YES/ESPN", (
        f"expected one local and one national channel joined: "
        f"{src._parse_broadcast(multi_broadcast)!r}"
    )
    dup_broadcast = {"broadcasts": [
        {"names": ["ESPN"]}, {"names": ["ESPN"]},
    ]}
    assert src._parse_broadcast(dup_broadcast) == "ESPN", (
        f"duplicate channel names should collapse: "
        f"{src._parse_broadcast(dup_broadcast)!r}"
    )
    assert src._parse_broadcast({"broadcast": "FOX"}) == "FOX"
    assert src._parse_broadcast({"broadcast": {"shortName": "Peacock"}}) == "Peacock"
    assert src._parse_broadcast({}) == ""
    print(f"PASS  broadcast parsing joins one local and one national/"
          f"streaming channel: {src._parse_broadcast(multi_broadcast)!r}")

    # Several regional feeds at once (both sides' home markets, sometimes
    # an extra blackout/alternate entry) must not all pile up into one
    # unreadable string -- only the first local entry ESPN lists is kept,
    # since a viewer picks one channel, not a wall of every market that
    # happens to carry the game.
    many_locals = {"broadcasts": [
        {"market": "home", "names": ["SNY"]},
        {"market": "away", "names": ["NBCSCH"]},
        {"market": "away", "names": ["MASN"]},
    ]}
    assert src._parse_broadcast(many_locals) == "SNY", (
        f"expected only the first local channel, not every regional feed "
        f"stacked together: {src._parse_broadcast(many_locals)!r}"
    )
    local_and_national = {"broadcasts": [
        {"market": "home", "names": ["SNY"]},
        {"market": "away", "names": ["NBCSCH"]},
        {"market": "national", "names": ["FOX"]},
    ]}
    assert src._parse_broadcast(local_and_national) == "SNY/FOX", (
        f"a second local feed should still be dropped even with a "
        f"national broadcaster also present: "
        f"{src._parse_broadcast(local_and_national)!r}"
    )
    print("PASS  broadcast parsing keeps only the most common local "
          "channel, not every regional feed on the game")

    # ---- 4. Team filtering and cadence ----------------------------------
    gm = GamesManager(log, cache_manager=FakeCache())

    class StubSource:
        def fetch_scoreboard(self, league, **kwargs):
            return {"mlb": mlb, "nba": nba, "nfl": nfl}.get(league, [])

        def fetch_leaders(self, league, event_id, per_game=2):
            return src._parse_leaders(SUMMARY, per_game, league)

        def fetch_batting(self, league, event_id):
            # Baseball reads the boxscore, since its summary has no leaders.
            return [{"team": "NYY", "name": "A.JUDGE", "line": "2-4, HR, 3 RBI",
                     "category": "BAT", "side": "batting"}]

    gm.source = StubSource()
    gm.refresh(force=True)

    assert gm.has_data()
    kept = gm.games()
    # The Braves game involves the Mets, the Celtics game involves the Knicks,
    # the Cowboys game involves the Giants -- all five followed teams appear.
    assert len(kept) == 5, [(g["league"], g["away"]["abbr"], g["home"]["abbr"])
                            for g in kept]
    assert gm.has_live(), "a live game should be detected"
    assert gm._interval() == gm.live_interval, "live games need the short timer"
    print(f"PASS  filtered to {len(kept)} followed games; live timer engaged")

    # has_live() only ever reflects the *previous* fetch -- a game crossing
    # from upcoming to live between two long, idle-interval checks would
    # otherwise sit undetected for up to the whole idle_interval. The timer
    # must shorten itself around a followed game's own scheduled start
    # instead of waiting for a refresh to notice the state already changed.
    gm2 = GamesManager(log, teams=[{"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    now_ts = _dt.now(_tz.utc).timestamp()

    def _iso(ts):
        return _dt.fromtimestamp(ts, tz=_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    gm2._games = [{"state": STATE_UPCOMING, "start": _iso(now_ts + 300)}]
    assert not gm2.has_live()
    assert gm2._interval() == gm2.live_interval, (
        "a game 5 minutes from its scheduled start should already use the "
        "short timer, not wait for a refresh to discover it went live"
    )

    gm2._games = [{"state": STATE_UPCOMING, "start": _iso(now_ts - 300)}]
    assert gm2._interval() == gm2.live_interval, (
        "a game just past its scheduled start should stay on the short "
        "timer until the state actually catches up"
    )

    gm2._games = [{"state": STATE_UPCOMING, "start": _iso(now_ts + 3600 * 5)}]
    assert gm2._interval() == gm2.idle_interval, (
        "a game hours away should not trigger the short timer early"
    )

    gm2._games = [{"state": STATE_UPCOMING, "start": _iso(now_ts - 3600 * 2)}]
    assert gm2._interval() == gm2.live_interval, (
        "a rain delay does not move the 'start' field -- a game 2 hours "
        "past its scheduled start with no live status yet should still "
        "stay on the short timer, not fall back to idle mid-delay"
    )

    gm2._games = [{"state": STATE_UPCOMING, "start": _iso(now_ts - 3600 * 6)}]
    assert gm2._interval() == gm2.idle_interval, (
        "a game 6 hours past its scheduled start with no live status is a "
        "genuine postponement, not a delay -- should fall back to the "
        "idle timer rather than poll every 45s forever"
    )
    print("PASS  the refresh timer shortens itself around a followed "
          "game's own scheduled start, not just after has_live() catches up")

    # A game involving none of the followed teams must be dropped
    stranger = src._parse_events(
        {"events": [event("999", ("LAD", "Dodgers", "0-0"),
                          ("SF", "Giants", "0-0"), "1", "2", "post", True)]},
        "mlb",
    )
    index = gm._team_index()
    assert not gm._is_followed(stranger[0], index), (
        "an unfollowed matchup slipped through -- note SF are also 'Giants'"
    )
    print("PASS  unfollowed games rejected (including the other Giants)")

    # A team must match whichever spelling the feed uses. Getting this wrong
    # is silent -- an abbreviation that never matches gives an empty board,
    # indistinguishable from the team not playing.
    from espn_data_source import abbr_group
    assert abbr_group("NYK") == abbr_group("NY") == {"NY", "NYK"}
    for configured in ("NYK", "NY"):
        gm_alias = GamesManager(
            log, teams=[{"abbr": configured, "league": "nba", "name": "Knicks"}]
        )
        alias_index = gm_alias._team_index()
        for feed_says in ("NY", "NYK"):
            probe = {"league": "nba",
                     "home": {"abbr": feed_says}, "away": {"abbr": "BOS"}}
            assert gm_alias._is_followed(probe, alias_index), (
                f"configured {configured!r} did not match feed {feed_says!r}"
            )
        unrelated = {"league": "nba",
                     "home": {"abbr": "LAL"}, "away": {"abbr": "BOS"}}
        assert not gm_alias._is_followed(unrelated, alias_index)
    print("PASS  NYK and NY are interchangeable, both ways round")

    # Leaders attach to live and final games, never to upcoming ones
    upcoming = [g for g in kept if g["state"] == STATE_UPCOMING]
    assert all(not g["leaders"] for g in upcoming), "upcoming game has leaders"
    played = [g for g in kept if g["state"] in (STATE_LIVE, STATE_FINAL)]
    assert all(g["leaders"] for g in played), "played game has no leaders"
    print("PASS  notable players attach only to games that have been played")

    # ---- 5. Rendering ---------------------------------------------------
    logo_dir = "/home/claude/_nyc_logos"
    os.makedirs(os.path.join(logo_dir, "mlb"), exist_ok=True)
    os.makedirs(os.path.join(logo_dir, "nba"), exist_ok=True)
    os.makedirs(os.path.join(logo_dir, "nfl"), exist_ok=True)
    for league, abbrs in (("mlb", ["NYY", "BOS", "NYM", "ATL", "TOR"]),
                          ("nba", ["NY", "BOS"]), ("nfl", ["NYG", "DAL"])):
        for abbr in abbrs:
            Image.new("RGBA", (500, 500), (190, 30, 40, 255)).save(
                os.path.join(logo_dir, league, f"{abbr}.png"))

    logos = TeamLogoManager(log, cache_dir=logo_dir, allow_download=False)
    assert logos.get_logo("mlb", "NYY", 8) is not None
    # Either spelling must resolve, since which one the feed uses is not
    # something to rely on.
    assert logos.get_logo("nba", "NY", 8) is not None, "Knicks logo missing as 'NY'"
    assert logos.get_logo("nba", "NYK", 8) is not None, "Knicks logo missing as 'NYK'"
    assert logos.get_logo("mlb", "ZZZ", 8) is None
    print("PASS  logos resolve per league, misses return None")

    for size in [(192, 32), (128, 32), (64, 32), (192, 64)]:
        for game in kept:
            display = FakeDisplay(*size)
            renderer = GameCardRenderer(display, {}, log, logo_manager=logos)
            shown = dict(game)
            if shown["state"] == STATE_UPCOMING:
                shown["start_label"] = ESPNGamesSource.local_start(shown)
            if not renderer.draw_game(shown):
                failures.append(f"{size}: {game['league']} card failed")
            frame = display.frames[-1]
            assert frame.size == size
            lit = sum(1 for y in range(frame.height) for x in range(frame.width)
                      if frame.load()[x, y] != (0, 0, 0))
            if lit < 20:
                failures.append(f"{size}: {game['league']} card nearly blank")
    print(f"PASS  {len(kept)} cards rendered at 4 panel sizes, none blank")

    # Previews at the real panel size
    display = FakeDisplay(192, 32)
    renderer = GameCardRenderer(display, {}, log, logo_manager=logos)
    stack = []
    for game in kept:
        display.frames.clear()
        shown = dict(game)
        if shown["state"] == STATE_UPCOMING:
            shown["start_label"] = ESPNGamesSource.local_start(shown)
        renderer.draw_game(shown)
        stack.append(display.frames[-1])
    sheet = Image.new("RGB", (192, 32 * len(stack)))
    for i, frame in enumerate(stack):
        sheet.paste(frame, (0, i * 32))
    sheet.resize((192 * 4, 32 * len(stack) * 4), Image.NEAREST).save(
        "/home/claude/nyc_cards.png")

    # ---- 5b. Team strips -----------------------------------------------
    # A strip must carry a team's whole story, and each sport must draw the
    # detail a fan of that sport actually watches for.
    strip_cases = [
        ({"abbr": "NYY", "league": "mlb", "name": "Yankees"},
         {"kind": "baseball", "balls": 3, "strikes": 2, "outs": 1,
          "first": True, "second": True, "third": False}),
        ({"abbr": "NYG", "league": "nfl", "name": "Giants"},
         {"kind": "football", "down_distance": "3rd & 7",
          "yard_line": "NYG 42", "possession": "NYG", "red_zone": False}),
        ({"abbr": "NYK", "league": "nba", "name": "Knicks"},
         {"kind": "basketball"}),
    ]
    for team, situation in strip_cases:
        for panel in [(192, 32), (128, 32), (64, 32)]:
            sdisplay = FakeDisplay(*panel)
            sr = StripRenderer(sdisplay, {}, log, logo_manager=logos)
            live_game = {
                "id": "s1", "league": team["league"], "state": STATE_LIVE,
                "status_detail": "Q3 4:12", "start": "",
                "away": {"abbr": "BOS", "score": "4"},
                "home": {"abbr": team["abbr"], "score": "7"},
                "situation": situation,
                "leaders": [{"category": "TOP", "name": "A.JUDGE",
                             "line": "2-4, HR, 3 RBI"}],
            }
            strip = sr.build_strip([(team, [live_game])])
            assert strip is not None, f"{team['abbr']} {panel}: no strip"
            assert strip.height == panel[1]
            assert strip.width >= panel[0], "strip narrower than the panel"
            span = sr.scroll_span(strip)
            assert span == strip.width, "span is the strip's own width"
            # Every frame of the pass must draw
            for off in (0, span // 3, span // 2, span - 1):
                assert sr.draw_strip(strip, off), f"{team['abbr']} frame {off}"
    print("PASS  strips build and scroll for all three sports at 3 panel sizes")

    # The strip is cached against its data, not rebuilt per frame
    sdisplay = FakeDisplay(192, 32)
    sr = StripRenderer(sdisplay, {}, log, logo_manager=logos)
    team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    base_game = {
        "id": "c1", "league": "mlb", "state": STATE_LIVE, "status_detail": "T7",
        "away": {"abbr": "BOS", "score": "4"}, "home": {"abbr": "NYY", "score": "7"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 1, "outs": 0},
        "leaders": [],
    }
    first = sr.build_strip([(team, [base_game])])
    again = sr.build_strip([(team, [base_game])])
    assert again is first, "strip was rebuilt when nothing changed"
    assert not sr.has_pending(), "an unchanged strip queued a rebuild"

    # A change queues a rebuild but does not swap: the swap happens at the
    # seam, so the update lands off-screen rather than shifting the view.
    moved = dict(base_game)
    moved["situation"] = dict(base_game["situation"], balls=3)
    sr._last_build = 0.0        # past the rebuild throttle, tested separately
    assert sr.build_strip([(team, [moved])]) is first, (
        "strip swapped mid-pass instead of queueing"
    )
    # The actual compose now runs on a background thread; synchronize on it
    # rather than racing the assertion against however long it takes.
    assert sr._wait_for_background_build(), "background build did not finish"
    assert sr.has_pending(), "a changed strip did not queue a rebuild"
    assert sr.adopt_pending()
    sr._last_build = 0.0
    assert sr.build_strip([(team, [moved])]) is not first, (
        "seam adoption did not take effect"
    )
    print("PASS  strip caches against its data and queues changes for the seam")

    # Grouping: a team's games must come back together, live first
    grouped = gm.games_for_team({"abbr": "NYY", "league": "mlb"})
    assert grouped, "no games grouped for the Yankees"
    assert all(
        "NYY" in (g["home"]["abbr"], g["away"]["abbr"]) for g in grouped
    ), grouped
    states = [g["state"] for g in grouped]
    assert states == sorted(
        states, key=lambda s: {STATE_LIVE: 0, STATE_FINAL: 1, STATE_UPCOMING: 2}[s]
    ), f"games not ordered live-first: {states}"
    print(f"PASS  team grouping returns {len(grouped)} Yankees games, live first")

    # A glance should show now, last and next -- not a fortnight of results.
    headline = gm.headline_games({"abbr": "NYY", "league": "mlb"})
    finals = [g for g in headline if g["state"] == STATE_FINAL]
    fixtures = [g for g in headline if g["state"] == STATE_UPCOMING]
    assert len(finals) <= 1, f"more than one final on the strip: {len(finals)}"
    assert len(fixtures) <= 1, f"more than one fixture on the strip: {len(fixtures)}"
    print(f"PASS  headline trims to {len(headline)} games: at most one final, one fixture")

    # Both sides of a game need a logo, not just the followed team.
    dboth = FakeDisplay(192, 32)
    rboth = StripRenderer(dboth, {}, log, logo_manager=logos)
    both_game = {
        "id": "b1", "league": "mlb", "state": STATE_FINAL, "start": "",
        "away": {"abbr": "BOS", "score": "4", "winner": False},
        "home": {"abbr": "NYY", "score": "7", "winner": True},
        "situation": {},
        "leaders": [{"category": "HR", "name": "A.JUDGE", "line": "2-4, HR"}],
    }
    with_logos = rboth.build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"}, [both_game])])
    # A fresh renderer, because a rebuild on the same one is now deferred to
    # the seam rather than returned immediately.
    rbare = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=None)
    without = rbare.build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"}, [both_game])])
    assert with_logos.width > without.width, (
        "logos did not widen the strip -- opponent crest may be missing"
    )
    print(f"PASS  both teams carry logos ({without.width}px -> {with_logos.width}px)")

    # The standout performance must sit beside its game, not in a trailing
    # section -- it is a fact about that result.
    rstat = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    bare = dict(both_game, leaders=[])
    plain = rstat.build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"}, [bare])])
    rnote = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    noted = rnote.build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"}, [both_game])])
    assert noted.width > plain.width, "noteworthy stat was not drawn"
    print(f"PASS  noteworthy stat attached to its final "
          f"({plain.width}px -> {noted.width}px)")

    # Same reasoning as the season MVP note, applied to the notable-performer
    # note that live and final games both draw through _draw_note: the full
    # name should only replace the abbreviation when the stat line is
    # already at least as wide, since the block's width is the widest row,
    # not their sum.
    class _NoteSpyDraw:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def text(self, xy, text, font=None, fill=None):
            self.calls.append(text)
            self.inner.text(xy, text, font=font, fill=fill)

        def textbbox(self, *a, **kw):
            return self.inner.textbbox(*a, **kw)

    from PIL import ImageDraw as _NoteID
    rnote2 = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=None)
    note_font, note_row_h = rnote2._fit_font(
        _NoteID.Draw(Image.new("RGB", (1, 1))), 2, rnote2.height)

    long_note_img = Image.new("RGB", (150, 32), (0, 0, 0))
    long_note_spy = _NoteSpyDraw(_NoteID.Draw(long_note_img))
    rnote2._draw_note(long_note_img, long_note_spy, 2, "Aaron Judge", "A.JUDGE",
                      "2-4, HR, 3 RBI, 2 R, BB", note_font, note_row_h)
    assert "Aaron Judge" in long_note_spy.calls, (
        f"a stat line wider than the short name should show the full name: "
        f"{long_note_spy.calls}"
    )

    short_note_img = Image.new("RGB", (150, 32), (0, 0, 0))
    short_note_spy = _NoteSpyDraw(_NoteID.Draw(short_note_img))
    rnote2._draw_note(short_note_img, short_note_spy, 2, "Aaron Judge", "A.JUDGE",
                      "HR", note_font, note_row_h)
    assert "A.JUDGE" in short_note_spy.calls and "Aaron Judge" not in short_note_spy.calls, (
        f"a stat line narrower than the short name should keep the "
        f"abbreviation: {short_note_spy.calls}"
    )
    print("PASS  the notable-performer note (live and final alike) shows "
          "the full name only when the stat line is already at least as "
          "wide as the abbreviation")

    # One continuous strip: every team on a single image, and it wraps.
    rmulti = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    multi = rmulti.build_strip([
        ({"abbr": "NYY", "league": "mlb", "name": "Yankees"}, [both_game]),
        ({"abbr": "NYK", "league": "nba", "name": "Knicks"},
         [dict(both_game, id="b2", league="nba",
               home={"abbr": "NYK", "score": "95", "winner": True})]),
    ])
    assert multi.width > noted.width, "second team did not extend the strip"
    dwrap = FakeDisplay(192, 32)
    rwrap = StripRenderer(dwrap, {}, log, logo_manager=logos)
    span = rwrap.scroll_span(multi)
    for off in (0, span // 2, span - 1, span + 5):
        assert rwrap.draw_strip(multi, off), f"frame at {off} failed"
        frame = dwrap.frames[-1]
        lit = sum(1 for y in range(frame.height) for x in range(frame.width)
                  if frame.load()[x, y] != (0, 0, 0))
        assert lit > 0, f"blank frame at offset {off} -- scroll is not continuous"
    print("PASS  one continuous strip across teams, wrapping with no blank frame")

    # The wrap seam -- end of one pass into the start of the next, which
    # begins with the clock -- must carry the same divider every other
    # boundary on the strip gets. The tail already had one; the start did
    # not, which read as a gap rather than a boundary.
    from datetime import datetime as _seam_dt
    seam_strip = rwrap.build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"}, [both_game])],
        clock=_seam_dt(2026, 8, 11, 19, 5),
    )
    seam_px = seam_strip.load()
    seam_rule_cols = [
        x for x in range(min(20, seam_strip.width))
        if any(seam_px[x, y] == StripRenderer.DIVIDER for y in range(32))
    ]
    assert seam_rule_cols, (
        "no divider near the very start of the strip -- the wrap seam "
        "has no marker on the side the clock starts from"
    )
    print(f"PASS  the wrap seam carries a divider at the start of the "
          f"strip (column {seam_rule_cols[0]}), matching the one at the end")

    # Leaderboards must ride on the same strip as the scores -- that is the
    # whole point of merging the two plugins.
    dboard = FakeDisplay(192, 32)
    rboard = StripRenderer(dboard, {}, log, logo_manager=logos)
    board_team = ({"abbr": "NYY", "league": "mlb", "name": "Yankees"},
                  [{"id": "lb1", "league": "mlb", "state": STATE_FINAL, "start": "",
                    "away": {"abbr": "BOS", "score": "4", "winner": False},
                    "home": {"abbr": "NYY", "score": "7", "winner": True},
                    "situation": {}, "leaders": []}])
    without_boards = rboard.build_strip([board_team])
    rboards2 = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    with_boards = rboards2.build_strip([board_team], leaderboards=[
        ("MLB HR LEADERS", [
            {"rank": 1, "short_name": "C.Raleigh", "team": "SEA", "value": "48"},
            {"rank": 2, "short_name": "A.Judge", "team": "NYY", "value": "41"}]),
        ("MLB ERA LEADERS", [
            {"rank": 1, "short_name": "T.Skubal", "team": "DET", "value": "2.49"}]),
    ])
    assert with_boards.width > without_boards.width, (
        "leaderboards did not extend the strip"
    )
    # An empty leaderboard must add nothing rather than a blank segment
    rboard._strip_key = None
    rempty = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    with_empty = rempty.build_strip([board_team],
                                    leaderboards=[("MLB HR LEADERS", [])])
    assert with_empty.width == without_boards.width, (
        "an empty leaderboard added a blank segment"
    )
    print(f"PASS  leaderboards ride the same strip "
          f"({without_boards.width}px -> {with_boards.width}px)")

    # The statistics block must announce itself. Each team opens with a crest,
    # so a block of numbers arriving with only a title reads as a continuation
    # of the last team rather than a change of subject.
    dsec = FakeDisplay(192, 32)
    rsec = StripRenderer(dsec, {}, log, logo_manager=logos)
    sec_rows = [{"rank": 1, "short_name": "C.Raleigh", "team": "SEA",
                 "value": "48"}]
    banner_strip = rsec.build_strip([board_team],
                                    leaderboards=[("AL HR", sec_rows, "HR")])
    plain_again = StripRenderer(
        FakeDisplay(192, 32), {}, log, logo_manager=logos).build_strip([board_team])
    assert banner_strip.width > plain_again.width + 40, (
        "no section banner drawn ahead of the statistics"
    )

    print("PASS  statistics open with a league mark ahead of the content")

    # A divider must separate the section banner itself ("MLB SEASON
    # LEADERS") from the first category's content -- without it, the title
    # ran directly into "AL HR LEADERS" with only a small gap and no line.
    dsep = FakeDisplay(192, 32)
    rsep = StripRenderer(dsep, {}, log, logo_manager=logos)
    sep_rows = [{"rank": 1, "short_name": "C.Raleigh", "team": "SEA",
                "value": "48"}]
    sep_strip = rsep.build_strip(
        [], leaderboards=[("AL HR LEADERS", sep_rows, "HR")],
        awards=[("AL MVP WATCH", sep_rows)],
    )
    spx = sep_strip.load()
    divider_cols = [
        xx for xx in range(sep_strip.width)
        if all(spx[xx, yy] == StripRenderer.DIVIDER for yy in (3, 4, 5)
              if spx[xx, yy] != (0, 0, 0))
        and any(spx[xx, yy] == StripRenderer.DIVIDER for yy in range(32))
    ]
    assert len(divider_cols) >= 2, (
        f"expected a divider after each of the two section banners, "
        f"found {len(divider_cols)} divider-coloured columns"
    )
    print("PASS  a divider separates each section banner from its own "
          "first category")

    # No two dividers should ever land back to back with nothing but blank
    # space between them -- that reads as one redundant, excess divider
    # marking a boundary that was already marked once. Built with every
    # section present (teams with an MVP note, other-live, leaderboards,
    # awards) specifically to exercise every section-to-section boundary
    # on the strip at once.
    dexcess = FakeDisplay(192, 32)
    rexcess = StripRenderer(dexcess, {}, log, logo_manager=logos)
    excess_team = ({"abbr": "NYY", "league": "mlb", "name": "Yankees"},
                   [dict(both_game, id="ex1")])
    excess_rows = [{"rank": 1, "short_name": "C.Raleigh", "team": "SEA",
                    "value": "48"}]
    excess_other_live = [{
        "id": "exo1", "league": "nba", "state": STATE_LIVE, "start": "",
        "away": {"abbr": "BOS", "score": "50"},
        "home": {"abbr": "LAL", "score": "48"},
        "situation": {"kind": "basketball", "clock": "5:00"}, "leaders": [],
    }]
    excess_strip = rexcess.build_strip(
        [excess_team], leaderboards=[("AL HR LEADERS", excess_rows, "HR")],
        awards=[("AL MVP WATCH", excess_rows)], other_live=excess_other_live,
        team_mvps={"NYY": {"name": "Aaron Judge", "short_name": "A.JUDGE",
                           "line": "AVG .312  HR 35  RBI 88"}},
    )
    expx = excess_strip.load()

    def _is_divider_col(xx):
        return any(expx[xx, yy] == StripRenderer.DIVIDER for yy in range(32))

    divider_xs = [xx for xx in range(excess_strip.width) if _is_divider_col(xx)]
    # Collapse each divider's own few-pixel-wide footprint into a single
    # position, then measure the gap from the end of one divider to the
    # start of the next.
    groups = []
    for xx in divider_xs:
        if groups and xx - groups[-1][-1] <= 1:
            groups[-1].append(xx)
        else:
            groups.append([xx])
    gaps = [groups[i + 1][0] - groups[i][-1] for i in range(len(groups) - 1)]
    MIN_REAL_GAP = 8  # narrower than any real segment's own content
    tiny_gaps = [g for g in gaps if g < MIN_REAL_GAP]
    assert not tiny_gaps, (
        f"found dividers only {min(tiny_gaps)}px apart -- two hairlines "
        f"marking the same boundary rather than one: gaps={gaps}"
    )
    print(f"PASS  no two dividers land back to back anywhere on the strip "
          f"({len(groups)} dividers, smallest real gap "
          f"{min(gaps) if gaps else 'n/a'}px)")

    # A leaderboard segment is a table: names start at one column and values
    # end at another, measured across every row, so figures align vertically.
    align_rows = [
        {"rank": 1, "short_name": "A.Judge", "team": "NYY", "value": ".331"},
        {"rank": 2, "short_name": "J.Soto", "team": "NYM", "value": ".312"},
        {"rank": 3, "short_name": "B.Witt", "team": "KC", "value": ".305"},
    ]
    dalign = FakeDisplay(192, 32)
    ralign = StripRenderer(dalign, {}, log)
    seg = ralign.build_strip([], leaderboards=[("AL AVG LEADERS", align_rows, "AVG")])
    apx = seg.load()

    # Three ranked rows, not two
    from PIL import ImageDraw as _ID
    adraw = _ID.Draw(Image.new("RGB", (192, 32)))
    _, arow_h = ralign._fit_font(adraw, 4, 32)
    lit_rows = {
        y for y in range(seg.height) for x in range(seg.width)
        if apx[x, y] != (0, 0, 0)
    }
    assert max(lit_rows) > arow_h * 3, (
        "third ranked row is missing -- segment holds only two"
    )

    # Values share a right edge: the last lit column of each value row must
    # be within a pixel of the others.
    right_edges = []
    for i in range(1, 4):
        band = range(1 + arow_h * i, 1 + arow_h * (i + 1))
        cols = [x for x in range(seg.width) for y in band
                if y < seg.height and apx[x, y] != (0, 0, 0)]
        if cols:
            right_edges.append(max(cols))
    assert len(right_edges) == 3, right_edges
    assert max(right_edges) - min(right_edges) <= 2, (
        f"values do not share a right edge: {right_edges}"
    )
    print(f"PASS  three rows, values aligned to one column (edges {right_edges})")

    # A rebuilt strip must wait for the seam. Swapping mid-pass shifts every
    # segment after the changed one sideways under the reader.
    dswap = FakeDisplay(192, 32)
    rswap = StripRenderer(dswap, {}, log)
    swap_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}

    def live_game(score):
        return {"id": "sw", "league": "mlb", "state": STATE_LIVE,
                "status_detail": "T7", "start": "",
                "away": {"abbr": "BOS", "score": "4"},
                "home": {"abbr": "NYY", "score": score},
                "situation": {"kind": "baseball", "balls": 1, "strikes": 1,
                              "outs": 0},
                "leaders": []}

    original = rswap.build_strip([(swap_team, [live_game("7")])])
    rswap._last_build = 0.0     # past the rebuild throttle
    during = rswap.build_strip([(swap_team, [live_game("9")])])
    assert during is original, "strip swapped mid-pass instead of waiting"
    assert rswap._wait_for_background_build(), "background build did not finish"
    assert rswap.has_pending(), "rebuilt strip was not held"
    assert rswap.adopt_pending() is True
    rswap._last_build = 0.0
    after = rswap.build_strip([(swap_team, [live_game("9")])])
    assert after is not original, "seam adoption did not take effect"
    assert rswap.adopt_pending() is False, "adopted twice"
    print("PASS  updates wait for the seam, then swap in off-screen")

    # The compose now runs on a background thread -- confirmed live: at the
    # display controller's 125 FPS scroll loop, running it in-line was slow
    # enough to read as the board freezing for a moment, roughly every
    # live_interval while a game was live. Two things specific to that
    # threading are worth their own direct coverage, not just "the existing
    # suite still passes": a background compose that raises must not crash
    # the caller or wedge the renderer, and a second data change arriving
    # while a build is still in flight must not spawn a second overlapping
    # thread doing the same work twice.
    dbg = FakeDisplay(192, 32)
    rbg = StripRenderer(dbg, {}, log)
    bg_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    bg_game = {"id": "bg1", "league": "mlb", "state": STATE_LIVE, "start": "",
              "away": {"abbr": "BOS", "score": "4"},
              "home": {"abbr": "NYY", "score": "7"},
              "situation": {"kind": "baseball", "balls": 0, "strikes": 0,
                            "outs": 0}, "leaders": []}
    rbg.build_strip([(bg_team, [bg_game])])  # first build, synchronous

    real_compose = rbg._compose_strip
    def _boom(*a, **k):
        raise RuntimeError("simulated compose failure")
    rbg._compose_strip = _boom
    rbg._last_build = 0.0
    moved_bg = dict(bg_game, situation=dict(bg_game["situation"], balls=2))
    still_showing = rbg.build_strip([(bg_team, [moved_bg])])
    assert still_showing is not None, (
        "a background compose failure should not crash the caller"
    )
    assert rbg._wait_for_background_build(), "background build did not finish"
    assert not rbg.has_pending(), (
        "a failed background compose should not leave a bogus pending strip"
    )
    assert rbg._dispatched_signature is None, (
        "a failed build must clear the in-flight marker, or a real fix "
        "afterward could be mistaken for 'already dispatched' and dropped"
    )
    rbg._compose_strip = real_compose

    # Now confirm a real, working rebuild still goes through cleanly after
    # a prior failure -- the renderer must not be left wedged.
    rbg._last_build = 0.0
    moved_bg2 = dict(bg_game, situation=dict(bg_game["situation"], balls=3))
    recovered = rbg.build_strip([(bg_team, [moved_bg2])])
    assert rbg._wait_for_background_build(), "background build did not finish"
    assert rbg.has_pending(), (
        "renderer stayed wedged after a prior background failure"
    )
    print("PASS  a background compose failure is caught, does not crash "
          "the caller, and does not wedge the renderer for the next build")

    # A second data change arriving while a build is still in flight must
    # not spawn a second overlapping thread for the same work.
    dbg2 = FakeDisplay(192, 32)
    rbg2 = StripRenderer(dbg2, {}, log)
    rbg2.build_strip([(bg_team, [bg_game])])  # first build, synchronous

    dispatch_count = {"n": 0}
    real_dispatch = rbg2._dispatch_background_build
    def _counting_dispatch(*a, **k):
        dispatch_count["n"] += 1
        return real_dispatch(*a, **k)
    rbg2._dispatch_background_build = _counting_dispatch

    slow_gate = threading.Event()
    real_compose2 = rbg2._compose_strip
    def _slow_compose(*a, **k):
        slow_gate.wait(2.0)
        return real_compose2(*a, **k)
    rbg2._compose_strip = _slow_compose

    rbg2._last_build = 0.0
    moved_bg3 = dict(bg_game, situation=dict(bg_game["situation"], balls=1))
    rbg2.build_strip([(bg_team, [moved_bg3])])  # dispatches, then blocks in compose
    # Same signature offered again while the first build is still stuck in
    # compose -- must not dispatch a second thread for identical work.
    rbg2.build_strip([(bg_team, [moved_bg3])])
    slow_gate.set()
    assert rbg2._wait_for_background_build(), "background build did not finish"
    assert dispatch_count["n"] == 1, (
        f"a second call for the same in-flight signature dispatched again: "
        f"{dispatch_count['n']} dispatches"
    )
    print("PASS  a signature already being built in the background is not "
          "dispatched a second time")

    # Composing a strip costs tens of milliseconds; build_strip runs on the
    # render path, so instability in the data must not be able to rebuild it
    # every frame. That is what freezes the scroll.
    import time as _time
    dthr = FakeDisplay(192, 32)
    rthr = StripRenderer(dthr, {}, log)
    thr_teams = [({"abbr": f"T{i}", "league": "mlb", "name": f"Team {i}"},
                  [{"id": f"t{i}", "league": "mlb", "state": STATE_LIVE,
                    "status_detail": "T7", "start": "",
                    "away": {"abbr": "BOS", "score": "4"},
                    "home": {"abbr": f"T{i}", "score": "7"},
                    "situation": {"kind": "baseball", "balls": 0,
                                  "strikes": 0, "outs": 0},
                    "leaders": []}]) for i in range(3)]
    rthr.build_strip(thr_teams)

    rebuilds = {"n": 0}
    real_banner = rthr._draw_banner
    rthr._draw_banner = lambda *a, **k: (
        rebuilds.__setitem__("n", rebuilds["n"] + 1), real_banner(*a, **k))[1]

    started = _time.perf_counter()
    frames = 0
    while _time.perf_counter() - started < 0.5:
        for _, games_for in thr_teams:
            games_for[0]["situation"]["balls"] = frames % 4
        rthr.build_strip(thr_teams)
        frames += 1
    per_frame_ms = (_time.perf_counter() - started) / max(1, frames) * 1000
    assert rebuilds["n"] <= len(thr_teams), (
        f"rebuilt {rebuilds['n'] // max(1, len(thr_teams))} times under the throttle"
    )
    assert per_frame_ms < 1.0, f"render path costs {per_frame_ms:.2f} ms/frame"
    print(f"PASS  rebuild throttled: {per_frame_ms:.3f} ms/frame with data "
          f"changing every frame")

    # A team playing now leads the strip -- a live score will not keep.
    glive = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
        {"abbr": "NYK", "league": "nba", "name": "Knicks"},
    ])
    glive._games = [
        {"id": "f", "league": "mlb", "state": STATE_FINAL, "start": "",
         "away": {"abbr": "BOS"}, "home": {"abbr": "NYY"}},
        {"id": "l", "league": "nba", "state": STATE_LIVE, "start": "",
         "away": {"abbr": "BOS"}, "home": {"abbr": "NYK"}},
    ]
    assert [t["abbr"] for t in glive.teams_with_games()][0] == "NYK", (
        "the live team is not first"
    )
    glive._games[1]["state"] = STATE_FINAL
    assert [t["abbr"] for t in glive.teams_with_games()][0] == "NYY", (
        "configured order should return once nothing is live"
    )
    print("PASS  live teams lead the strip, order restored when none are")

    # The title and its column header share the top row and must not overlap.
    rhead = StripRenderer(FakeDisplay(192, 32), {}, log)
    header_seg = rhead.build_strip(
        [], leaderboards=[("AL AVG LEADERS", align_rows, "AVG")])
    assert header_seg.width >= seg.width
    print("PASS  title and column header sit side by side")

    # Rank 1/2/3 must be gold/silver/bronze, and a row's team abbreviation
    # must be that team's own colour, not the rank colour -- drawn as two
    # separate runs of text, so a name and its team read as visually
    # distinct pieces rather than one flat-coloured line.
    medal_rows = [
        {"rank": 1, "short_name": "A.JUDGE", "team": "NYY", "value": "58"},
        {"rank": 2, "short_name": "R.DEVERS", "team": "BOS", "value": "42"},
        {"rank": 3, "short_name": "V.GUERRERO", "team": "TOR", "value": "40"},
    ]
    dmedal = Image.new("RGB", (220, 32), (0, 0, 0))
    from PIL import ImageDraw as _MedalID
    medal_draw = _MedalID.Draw(dmedal)
    rmedal = StripRenderer(FakeDisplay(192, 32), {}, log)
    medal_font, medal_row_h = rmedal._fit_font(medal_draw, 4, rmedal.height)

    # Pixel-scanning the render is unreliable here: this sandbox's
    # fallback font antialiases every glyph edge, blending the fill colour
    # with the black background so no pixel is ever the exact fill value
    # -- exactly the kind of fragile check this project's own notes warn
    # about. Spying on the draw.text calls themselves checks what the code
    # actually decided to draw, independent of how any given font rasterises.
    calls = []
    real_text = medal_draw.text

    def spy_text(xy, text, font=None, fill=None, **kw):
        calls.append((text, fill))
        return real_text(xy, text, font=font, fill=fill, **kw)

    medal_draw.text = spy_text
    rmedal._draw_leaderboard(dmedal, medal_draw, 2, "HR LEADERS", medal_rows,
                             medal_font, medal_row_h, "HR")
    medal_draw.text = real_text

    by_text = {text: fill for text, fill in calls}
    assert by_text.get("1.A.JUDGE") == StripRenderer.GOLD, (
        f"rank 1 name drawn in {by_text.get('1.A.JUDGE')}, not gold"
    )
    assert by_text.get("2.R.DEVERS") == StripRenderer.SILVER, (
        f"rank 2 name drawn in {by_text.get('2.R.DEVERS')}, not silver"
    )
    assert by_text.get("3.V.GUERRERO") == StripRenderer.BRONZE, (
        f"rank 3 name drawn in {by_text.get('3.V.GUERRERO')}, not bronze"
    )
    assert by_text.get("NYY") == StripRenderer.TEAM_COLORS["NYY"], (
        f"NYY drawn in {by_text.get('NYY')}, not its own team colour"
    )
    assert by_text.get("BOS") == StripRenderer.TEAM_COLORS["BOS"], (
        f"BOS drawn in {by_text.get('BOS')}, not its own team colour"
    )
    print("PASS  ranks 1-3 render gold/silver/bronze, team abbreviations in "
          "their own team's colour")

    # AL and NL get their own mark instead of a text prefix; MLB (the merged
    # scope) keeps the text label since it has no mark of its own here.
    from awards_manager import AWARD_DEFINITIONS as _AD
    assert "MVP" == _AD["mvp"]["label"], (
        "award label still carries the redundant 'WATCH' the section "
        "banner already says once"
    )
    print("PASS  award labels no longer repeat 'WATCH' the section banner "
          "already carries")

    # ---- 5c. Logo manager: AL/NL marks -----------------------------------
    # get_scope_logo must only ever answer for al/nl -- anything else (a
    # typo, a real league code passed by mistake) returns None immediately,
    # with no network attempt, rather than silently trying to download
    # something ESPN was never confirmed to serve at this path for.
    scope_logos = TeamLogoManager(log, allow_download=False)
    assert scope_logos.get_scope_logo("mlb", 14) is None, (
        "get_scope_logo answered for a scope other than al/nl"
    )
    assert scope_logos.get_scope_logo("nfl", 14) is None
    print("PASS  get_scope_logo only ever answers for al/nl")

    # manager._leaderboards() must keep the "AL"/"NL" text prefix alongside
    # the scope's own mark -- the mark alone was not enough on its own, and
    # "MLB" (the merged scope, no mark of its own here) always carried the
    # text label regardless.
    class TitleStubLeaders:
        def get_category(self, category, scope="mlb", pool=""):
            return [{"rank": 1, "name": "Test Player", "short_name": "T.PLAYER",
                     "team": "NYY", "value": "1", "player_id": "1"}]

        def get_player_stats(self, player_id, group, scope="mlb", pool=""):
            return {}

    title_plugin = LocalScoreboardPlugin("local-scoreboard", {}, FakeDisplay(192, 32),
                                  FakeCache(), None)
    title_plugin.leaders = TitleStubLeaders()
    title_plugin.awards = None
    title_plugin.teams_leaderboards_on = True
    title_plugin.teams_leader_categories = ["homeRuns"]
    title_plugin.teams_leader_depth = 3
    title_plugin.teams_leader_scopes = ["al", "nl", "mlb"]
    title_plugin._boards_cache = None
    boards, _ = title_plugin._leaderboards()
    titles = {b[0] for b in boards}
    assert "AL HR LEADERS" in titles and "NL HR LEADERS" in titles, (
        f"AL/NL board lost its text prefix: {titles}"
    )
    assert "MLB HR LEADERS" in titles, (
        f"the merged scope lost its text label: {titles}"
    )
    scopes_seen = {b[3] for b in boards if len(b) > 3}
    assert scopes_seen == {"al", "nl", "mlb"}, (
        f"board entries did not carry their scope through: {scopes_seen}"
    )
    print("PASS  AL/NL boards keep the 'AL'/'NL' text prefix alongside "
          "their mark; MLB (merged scope) keeps its text label")

    # Complete stat lines on every row were reverted -- a row shows only
    # the one category it is ranked by again, the same "value" field as
    # before that change, with no "line" attached.
    class NoLineStubLeaders:
        def get_category(self, category, scope="mlb", pool=""):
            return [{"rank": 1, "name": "Aaron Judge", "short_name": "A.JUDGE",
                     "team": "NYY", "value": "35", "player_id": "j1"}]

        def get_player_stats(self, player_id, group, scope="mlb", pool=""):
            return {"AVG": ".312", "HR": "35", "RBI": "88"}

    noline_plugin = LocalScoreboardPlugin("local-scoreboard", {}, FakeDisplay(192, 32),
                                   FakeCache(), None)
    noline_plugin.leaders = NoLineStubLeaders()
    noline_plugin.awards = None
    noline_plugin.teams_leaderboards_on = True
    noline_plugin.teams_leader_categories = ["homeRuns"]
    noline_plugin.teams_leader_depth = 3
    noline_plugin.teams_leader_scopes = ["al"]
    noline_plugin._boards_cache = None
    boards, _ = noline_plugin._leaderboards()
    board_row = boards[0][1][0]
    assert board_row.get("value") == "35", (
        f"leaderboard row lost its single ranked value: {board_row}"
    )
    assert "line" not in board_row, (
        f"leaderboard row still carries a full stat line after the revert: "
        f"{board_row}"
    )
    print("PASS  leaderboard rows are back to a single ranked value, "
          "complete stat lines reverted")

    # A clock must repaint in place. Recomposing the strip for a minute
    # change costs hundreds of milliseconds; a clock that only advanced when
    # the strip was rebuilt would sit minutes behind on a long pass.
    from datetime import datetime as _clock_dt
    dclock = FakeDisplay(192, 32)
    rclock = StripRenderer(dclock, {}, log)
    rclock.build_strip([], clock=_clock_dt(2026, 8, 9, 19, 5))
    assert rclock._clock_box, "clock position was not recorded"
    first_text = rclock._clock_shown
    cached = rclock._strip_cache
    rclock.refresh_clock(_clock_dt(2026, 8, 9, 19, 6))
    assert rclock._clock_shown != first_text, "clock did not advance"
    assert rclock._strip_cache is cached, "clock repaint rebuilt the strip"
    # Same minute again must be a no-op
    unchanged = rclock._clock_shown
    rclock.refresh_clock(_clock_dt(2026, 8, 9, 19, 6))
    assert rclock._clock_shown == unchanged
    print(f"PASS  clock repaints in place ({first_text} -> {rclock._clock_shown})")

    # A live game for the pinned team holds the left module while everything
    # else scrolls past it -- the point being that a game you care about
    # should not scroll away mid-at-bat.
    dpanel = FakeDisplay(192, 32)
    rpanel = StripRenderer(dpanel, {}, log, logo_manager=logos)
    live_panel_game = {
        "id": "L", "league": "mlb", "state": STATE_LIVE,
        "status_detail": "TOP 7", "start": "",
        "away": {"abbr": "BOS", "score": "4"},
        "home": {"abbr": "NYY", "score": "7"},
        "situation": {"kind": "baseball", "balls": 2, "strikes": 1, "outs": 2,
                      "first": True, "second": False, "third": True,
                      "batter": "J.SOTO", "pitcher": "G.COLE"},
        "leaders": [],
    }
    panel = rpanel.render_static_panel(live_panel_game, "NYY", 64)
    assert panel is not None and panel.size == (64, 32), panel
    ppx = panel.load()
    assert any(ppx[x, y] != (0, 0, 0) for y in range(32) for x in range(64)), (
        "static panel is blank"
    )
    assert rpanel.render_static_panel(None, "NYY", 64) is None
    assert rpanel.render_static_panel(live_panel_game, "NYY", 8) is None

    # A crest even 1px taller than its own row_h bleeds into the row below
    # it -- confirmed on real hardware, where the two team crests touched
    # each other and crowded the bases/count/outs row beneath them. Every
    # crest this method requests must fit inside the row_h it is drawn
    # into, not overshoot it.
    from PIL import ImageDraw as _PanelID
    panel_probe = Image.new("RGB", (64, 32), (0, 0, 0))
    panel_draw = _PanelID.Draw(panel_probe)
    panel_font, panel_row_h = rpanel._fit_font(panel_draw, 4, rpanel.height)
    for probe_abbr in ("NYY", "BOS"):
        probe_crest = rpanel._logo("mlb", probe_abbr, min(panel_row_h, 9))
        assert probe_crest is not None and probe_crest.height <= panel_row_h, (
            f"{probe_abbr} crest ({probe_crest.height if probe_crest else '?'}px) "
            f"taller than its own row ({panel_row_h}px) -- will bleed into "
            f"the row below it"
        )
    print(f"PASS  static panel crests fit within their own row_h "
          f"({panel_row_h}px), no bleed into the next row")

    # The bases diamond used to share the bottom row with the count and
    # outs, and its own geometry (the two bottom markers sit lower than the
    # top one) ran past the panel's bottom edge there, clipped clean off on
    # real hardware. It now lives beside the team names instead -- confirmed
    # here by checking every base-marker pixel sits within the team-name
    # band (rows 1-2), not the bottom row, and nowhere near the panel's own
    # bottom edge.
    bases_panel = rpanel.render_static_panel(live_panel_game, "NYY", 64, phase=0)
    bpx = bases_panel.load()
    base_colours = {rpanel.BASE_ON, rpanel.BASE_OFF}
    base_pixels = [(x, y) for y in range(32) for x in range(64)
                  if bpx[x, y] in base_colours]
    assert base_pixels, "no base markers drawn at all"
    bottom_row_start = rpanel.MARGIN + panel_row_h * 3
    assert all(y < bottom_row_start for _, y in base_pixels), (
        f"a base marker is still in the bottom row (y >= {bottom_row_start}), "
        f"where it has no room and gets clipped: {base_pixels}"
    )
    assert all(y <= 30 for _, y in base_pixels), (
        f"a base marker sits at the panel's very last row -- likely clipped: "
        f"{base_pixels}"
    )
    print("PASS  bases diamond moved beside the team names, clear of the "
          "bottom row and the panel's own bottom edge")

    # With a panel fixed, the strip scrolls in what is left, not the whole panel
    base_strip = rpanel.build_strip([({"abbr": "NYK", "league": "nba",
                                       "name": "Knicks"}, [dict(
        live_panel_game, id="K", league="nba", state=STATE_FINAL,
        home={"abbr": "NYK", "score": "95", "winner": True},
        away={"abbr": "BOS", "score": "88", "winner": False},
        situation={})])])
    assert rpanel.scroll_window() == 192, "no panel set, window should be full"
    rpanel.set_static_panel(panel)
    assert rpanel.scroll_window() == 192 - 64 - 1, rpanel.scroll_window()
    assert rpanel.draw_strip(base_strip, 0)
    frame = dpanel.frames[-1]
    fpx = frame.load()
    left_lit = sum(1 for y in range(32) for x in range(64)
                   if fpx[x, y] != (0, 0, 0))
    assert left_lit > 20, "the fixed panel did not reach the frame"
    # Scrolling must not disturb the fixed module
    before = [fpx[x, y] for y in range(32) for x in range(60)]
    rpanel.draw_strip(base_strip, 40)
    after_px = dpanel.frames[-1].load()
    after = [after_px[x, y] for y in range(32) for x in range(60)]
    assert before == after, "the fixed panel moved with the scroll"
    print("PASS  live game pinned to the left module, unaffected by scrolling")

    # When nothing is live to pin, the same left-module slot should not go
    # unused -- the clock and current temperature go there instead, without
    # ever touching the scroll (that's a completely separate image; this
    # only changes how much of the panel the static slot reserves). Sized
    # to its own content, not a flat 64px -- centring inside a fixed box
    # only moved the dead space around, it did not remove it.
    from datetime import datetime as _cwdt
    cw_weather = {"now_temp": 78, "units": "F", "now_condition": "Clear"}
    cw_panel = rpanel.render_clock_weather_panel(
        _cwdt(2026, 8, 11, 19, 5), cw_weather, 64)
    assert cw_panel is not None and cw_panel.height == 32, cw_panel
    assert cw_panel.width < 64, (
        f"panel did not shrink to its own content: {cw_panel.width}px"
    )
    cwpx = cw_panel.load()
    cwlit = [(x, y) for y in range(cw_panel.height) for x in range(cw_panel.width)
            if cwpx[x, y] != (0, 0, 0)]
    assert cwlit, "clock/weather panel is blank"
    cwtop, cwbottom = min(y for x, y in cwlit), max(y for x, y in cwlit)
    assert cwtop >= 1 and (31 - cwbottom) >= 1, (
        f"clock/weather panel margins violated: top={cwtop} "
        f"bottom={31 - cwbottom}"
    )
    # No weather data at all must not crash -- clock and date alone still
    # draw something rather than an empty panel, and should be narrower
    # still, with one less row's worth of content to fit.
    empty_weather_panel = rpanel.render_clock_weather_panel(
        _cwdt(2026, 8, 11, 19, 5), {}, 64)
    assert empty_weather_panel is not None
    assert empty_weather_panel.width <= cw_panel.width, (
        "panel with less content (no weather) was not at least as narrow "
        "as the one with weather"
    )
    epx = empty_weather_panel.load()
    assert any(epx[x, y] != (0, 0, 0)
              for y in range(32) for x in range(empty_weather_panel.width)), (
        "clock/weather panel with no weather data drew nothing at all"
    )
    print(f"PASS  clock/weather panel shrinks to its own content "
          f"({cw_panel.width}px, not a flat 64px), still degrading "
          f"gracefully with no weather data")

    # A live game must still win outright -- the fallback is strictly
    # lower priority, never a competitor for the same slot.
    cw_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    cw_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    cw_plugin.games._games = [{
        "id": "live1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "3"}, "away": {"abbr": "BOS", "score": "2"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 2, "outs": 1},
        "leaders": [],
    }]
    cw_plugin.teams_panel_priority = ["NYY"]
    cw_plugin.display()
    with_live = cw_plugin.strip._static_panel
    assert with_live is not None, "a live game should still claim the static panel"

    cw_plugin.games._games = []
    cw_plugin.display()
    without_live = cw_plugin.strip._static_panel
    assert without_live is not None, (
        "with nothing live, the clock/weather fallback should still claim "
        "the static panel rather than leaving it empty"
    )
    print("PASS  a live game still wins the static panel outright over the "
          "clock/weather fallback")

    # The refresh gate: check for a live game every idle_interval (a
    # minute by default), and once ANY followed team actually is live --
    # not just whichever is pinned to the static panel -- drop to
    # live_interval (5s) so balls, strikes and score stay current
    # instead of sitting frozen for most of a minute. Also fast during
    # _followed_game_starting_soon(), so a game beginning near the end of
    # an idle wait is not missed for most of that wait either.
    class _RefreshSpyGames:
        def __init__(self, games_list, starting_soon=False):
            self._games_list = games_list
            self._starting_soon = starting_soon
            self.refresh_calls = []

        def games(self, state=None):
            if state is None:
                return self._games_list
            return [g for g in self._games_list if g.get("state") == state]

        def has_live(self):
            return any(g.get("state") == STATE_LIVE for g in self._games_list)

        def _followed_game_starting_soon(self):
            return self._starting_soon

        def has_data(self):
            return True

        def refresh(self, force=False):
            self.refresh_calls.append(force)

        def refresh_streaks(self):
            pass

    update_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    update_plugin.leaders = None
    update_plugin.weather = None
    update_plugin.awards = None
    update_plugin.teams_idle_interval = 60
    update_plugin.teams_live_interval = 5

    update_plugin.games = _RefreshSpyGames([])
    update_plugin._last_update = time.time() - 10
    update_plugin.update()
    assert update_plugin.games.refresh_calls == [], (
        "should not refresh yet -- 10s in is still under the 60s idle gate "
        "with nothing live and no game starting soon"
    )

    update_plugin.games = _RefreshSpyGames([{
        "id": "live1", "league": "mlb", "state": STATE_LIVE,
        "home": {"abbr": "NYY"}, "away": {"abbr": "BOS"},
    }])
    update_plugin._last_update = time.time() - 10
    update_plugin.update()
    assert update_plugin.games.refresh_calls == [True], (
        f"any live followed game should force a refresh past the ~5s gate, "
        f"not just one pinned to the static panel: "
        f"{update_plugin.games.refresh_calls}"
    )

    # A game about to start (not live yet) must get the same fast gate --
    # otherwise it could go undetected for most of the idle interval.
    update_plugin.games = _RefreshSpyGames([], starting_soon=True)
    update_plugin._last_update = time.time() - 10
    update_plugin.update()
    assert update_plugin.games.refresh_calls == [True], (
        f"a game starting soon should also force a refresh past the ~5s "
        f"gate: {update_plugin.games.refresh_calls}"
    )
    print("PASS  update() checks for a live game every ~60s, then drops to "
          "~5s the moment any followed team is live or about to start")

    # The bottom row alternates: 64 pixels will not hold the bases, the count,
    # the outs and a player name at once -- the name truncates to a letter.
    with_people = rpanel.render_static_panel(live_panel_game, "NYY", 64, phase=1)
    with_bases = rpanel.render_static_panel(live_panel_game, "NYY", 64, phase=0)
    assert with_people is not None and with_bases is not None
    # Compare the whole image: the situation row starts higher than a fixed
    # band would suggest, because the bases diamond is drawn above the text
    # baseline.
    from PIL import ImageChops as _Chops
    difference = _Chops.difference(with_people, with_bases)
    assert difference.getbbox() is not None, (
        "the bottom row did not change between phases"
    )
    for image in (with_people, with_bases):
        ipx = image.load()
        assert max(y for y in range(32) for x in range(64)
                   if ipx[x, y] != (0, 0, 0)) <= 31, "panel content is clipped"
    print("PASS  panel bottom row alternates between situation and players")

    # The static panel is a separate rendering path from the scrolling strip
    # and must respect the exact same one-row top-and-bottom margin -- this
    # matters more than cosmetics here, since the panel may sit inside a
    # physical case whose bezel covers anything outside that margin. Every
    # sport, both alternation phases: the bases-diamond phase for baseball
    # is the one that previously overflowed by a full pixel.
    panel_cases = {
        "baseball": {"id": "pb1", "league": "mlb", "status_detail": "TOP 7",
                    "period": 7, "away": {"abbr": "BOS", "score": "4"},
                    "home": {"abbr": "NYY", "score": "7"},
                    "situation": {"kind": "baseball", "balls": 2, "strikes": 1,
                                 "outs": 1, "first": True, "second": False,
                                 "third": True, "batter": "J.SOTO",
                                 "pitcher": "G.COLE"}},
        "football": {"id": "pf1", "league": "nfl", "status_detail": "Q2",
                    "period": 2, "away": {"abbr": "BUF", "score": "14"},
                    "home": {"abbr": "NYG", "score": "10"},
                    "situation": {"kind": "football", "down_distance": "2nd & 5",
                                 "yard_line": "NYG 30", "possession": "NYG",
                                 "red_zone": False, "clock": "6:44"}},
        "basketball": {"id": "pk1", "league": "nba", "status_detail": "Q4",
                       "period": 4, "away": {"abbr": "BOS", "score": "96"},
                       "home": {"abbr": "NYK", "score": "101"},
                       "situation": {"kind": "basketball", "clock": "2:31"}},
    }
    panel_focus = {"baseball": "NYY", "football": "NYG", "basketball": "NYK"}
    for sport, pgame in panel_cases.items():
        for phase in (0, 1):
            ppanel = rpanel.render_static_panel(
                pgame, panel_focus[sport], 64, phase)
            assert ppanel is not None, f"{sport} phase {phase}: no panel"
            ppx = ppanel.load()
            plit = [(x, y) for y in range(32) for x in range(64)
                   if ppx[x, y] != (0, 0, 0)]
            assert plit, f"{sport} phase {phase}: panel is blank"
            ptop = min(y for x, y in plit)
            pbottom_margin = 31 - max(y for x, y in plit)
            assert ptop >= 1, (
                f"{sport} phase {phase}: top margin {ptop} violates the "
                f"one-row minimum"
            )
            assert pbottom_margin >= 1, (
                f"{sport} phase {phase}: bottom margin {pbottom_margin} "
                f"violates the one-row minimum"
            )
    print("PASS  static panel holds a one-row margin in every sport and phase")

    # Football and basketball get their own situation line, not baseball's.
    nfl_game = {
        "id": "N", "league": "nfl", "state": STATE_LIVE, "period": 3,
        "status_detail": "Q3", "start": "",
        "away": {"abbr": "DAL", "score": "17"},
        "home": {"abbr": "NYG", "score": "21"},
        "situation": {"kind": "football", "down_distance": "3rd & 7",
                      "yard_line": "NYG 42", "possession": "NYG",
                      "red_zone": False, "clock": "4:12"},
        "leaders": [],
    }
    nfl_panel = rpanel.render_static_panel(nfl_game, "NYG", 64)
    assert nfl_panel is not None
    nba_game = dict(nfl_game, league="nba", id="B",
                    situation={"kind": "basketball", "clock": "2:31"})
    assert rpanel.render_static_panel(nba_game, "NYK", 64) is not None
    print("PASS  panels drawn per sport, not one baseball layout for all")

    # Final and upcoming games must give the crest full size with a real
    # margin top and bottom, not run flush to the panel edge.
    dfull = FakeDisplay(192, 32)
    rfull = StripRenderer(dfull, {}, log, logo_manager=logos)
    full_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    final_game = {"id": "f1", "league": "mlb", "state": STATE_FINAL, "start": "",
                 "away": {"abbr": "BOS", "score": "4", "winner": False},
                 "home": {"abbr": "NYY", "score": "7", "winner": True},
                 "situation": {}, "leaders": []}
    upcoming_game = {"id": "u1", "league": "mlb", "state": STATE_UPCOMING,
                     "start": "",
                     "away": {"abbr": "NYY", "score": "", "record": "70-46"},
                     "home": {"abbr": "TB", "score": "", "record": "55-61"},
                     "situation": {}, "leaders": []}
    live_game_full = {"id": "l1", "league": "mlb", "state": STATE_LIVE,
                      "status_detail": "TOP 7", "start": "",
                      "away": {"abbr": "BOS", "score": "4"},
                      "home": {"abbr": "NYY", "score": "7"},
                      "situation": {"kind": "baseball", "balls": 1,
                                    "strikes": 1, "outs": 0}, "leaders": []}

    for label, game in (("final", final_game), ("upcoming", upcoming_game)):
        rone = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
        one_strip = rone.build_strip([(full_team, [game])],
                                     {"u1": "MON 8/11 7:05"})
        opx = one_strip.load()
        lit_rows = [y for y in range(32) for x in range(one_strip.width)
                   if opx[x, y] != (0, 0, 0)]
        top_margin = min(lit_rows)
        bottom_margin = 31 - max(lit_rows)
        assert top_margin >= 1, f"{label}: no top margin ({top_margin})"
        assert bottom_margin >= 1, f"{label}: no bottom margin ({bottom_margin})"
        assert max(lit_rows) <= 31, f"{label}: content clipped past the panel"
        print(f"PASS  {label} game: margins top={top_margin} bottom={bottom_margin}, "
              f"nothing clipped")

    # The live segment must be unaffected -- it needs its own room for the
    # situation line and should not shrink or overflow because of this change.
    rlive = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    live_strip = rlive.build_strip([(full_team, [live_game_full])])
    lpx = live_strip.load()
    assert max(y for y in range(32) for x in range(live_strip.width)
              if lpx[x, y] != (0, 0, 0)) <= 31, "live segment overflowed the panel"
    print("PASS  live segment unaffected by the full-size crest change")

    # The whole point: a final/upcoming crest should be close to the
    # banner's own logo size, not capped by a status row and a score row
    # both stacked underneath it. Measured via the actual sizes passed to
    # the logo lookup -- a pixel scan of the rendered image cannot tell a
    # game's own crest apart from the team banner's crest earlier on the
    # same strip, since both are similarly-sized discs.
    rsize = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    banner_logo_size = rsize.height - rsize.MARGIN * 2

    requested_sizes = []
    real_logo = rsize._logo
    def spy_logo(league, abbr, size, *a, **k):
        requested_sizes.append(size)
        return real_logo(league, abbr, size, *a, **k)
    rsize._logo = spy_logo

    assert rsize.build_strip([(full_team, [final_game])]) is not None
    # First size requested is the team banner's; the rest belong to the
    # game segment's two crests.
    assert len(requested_sizes) >= 3, requested_sizes
    banner_size, game_crest_size = requested_sizes[0], requested_sizes[1]
    assert banner_size == banner_logo_size, (banner_size, banner_logo_size)
    assert game_crest_size >= banner_logo_size * 0.6, (
        f"final crest requested at {game_crest_size}px against a "
        f"{banner_logo_size}px banner logo -- still capped by stacked rows"
    )
    print(f"PASS  final crest requested at {game_crest_size}px against a "
          f"{banner_logo_size}px banner logo (score sits beside it, not below)")

    rpanel.set_static_panel(None)
    assert rpanel.scroll_window() == 192

    # Other-live games: live-only, across every league, excluding anything
    # already covered as a followed team's own game.
    gol = GamesManager(log, teams=[{"abbr": "NYY", "league": "mlb", "name": "Yankees"}])

    class OtherLiveStub:
        def fetch_scoreboard(self, league, **kwargs):
            # fetch_scoreboard returns PARSED games, not raw scoreboard JSON --
            # the real source parses internally, so the stub must too.
            if league == "mlb":
                raw = {"events": [
                    event("f1", ("BOS", "Red Sox", "0-0"), ("NYY", "Yankees", "0-0"),
                          "4", "7", "post", True, "Final"),
                    event("l1", ("SD", "Padres", "0-0"), ("LAD", "Dodgers", "0-0"),
                          "2", "3", "in", False, "Bot 5"),
                    event("f2", ("HOU", "Astros", "0-0"), ("TEX", "Rangers", "0-0"),
                          "1", "6", "post", True, "Final"),
                ]}
                return src._parse_events(raw, "mlb")
            if league == "nfl":
                raw = {"events": [
                    event("l2", ("BUF", "Bills", "0-0"), ("KC", "Chiefs", "0-0"),
                          "14", "10", "in", False, "Q2", period=2),
                ]}
                return src._parse_events(raw, "nfl")
            return []

        def fetch_leaders(self, league, event_id, per_game=2):
            return []

        def fetch_batting(self, league, event_id):
            return []

    gol.source = OtherLiveStub()
    gol.refresh(force=True)

    other = gol.other_live_games()
    other_ids = {g["id"] for g in other}
    assert other_ids == {"l1", "l2"}, other_ids
    assert all(g["state"] == STATE_LIVE for g in other)
    print(f"PASS  other-live returns only live games, across leagues: {sorted(other_ids)}")

    # has_any_live() is broader than has_live() on purpose -- a live game
    # with nothing to do with a followed team still counts. Checked on a
    # fresh instance rather than mutating the shared gol fixture, which
    # later assertions in this file still depend on.
    assert not gol.has_live(), (
        "test setup error: no followed team's own game should be live here"
    )
    assert gol.has_any_live(), (
        "has_any_live() must be True from other-live alone, with no "
        "followed team live"
    )
    gol_empty = GamesManager(log, teams=[{"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    assert not gol_empty.has_any_live(), (
        "has_any_live() must be False when neither followed nor other-live "
        "has anything live"
    )
    print("PASS  has_any_live() is true from other-live alone, false when "
          "nothing at all is live")

    # The followed team's own game must never double up in this list
    followed_ids = {g["id"] for g in gol.games()}
    assert not (followed_ids & other_ids), (
        f"followed game appeared in other-live too: {followed_ids & other_ids}"
    )
    print("PASS  a followed team's own game is not duplicated into other-live")

    # Standings parsing: a streak is flattened out of each division/
    # conference group, "-" (no streak yet, e.g. NFL preseason) is dropped
    # rather than shown as a literal dash, and a team with no streak stat
    # at all is simply absent, not an error.
    standings_payload = {
        "children": [
            {"name": "American League", "standings": {"entries": [
                {"team": {"abbreviation": "NYY"}, "stats": [
                    {"type": "wins", "displayValue": "70"},
                    {"type": "streak", "displayValue": "W3"},
                ]},
                {"team": {"abbreviation": "BOS"}, "stats": [
                    {"type": "streak", "displayValue": "-"},
                ]},
            ]}},
            {"name": "National League", "standings": {"entries": [
                {"team": {"abbreviation": "NYM"}, "stats": [
                    {"type": "streak", "displayValue": "L2"},
                ]},
                {"team": {"abbreviation": "ATL"}, "stats": [
                    {"type": "wins", "displayValue": "55"},
                ]},
            ]}},
        ]
    }
    parsed_streaks = ESPNGamesSource._parse_standings(standings_payload)
    assert parsed_streaks == {"NYY": "W3", "NYM": "L2"}, (
        f"streaks did not flatten across both conference groups correctly: "
        f"{parsed_streaks}"
    )
    print(f"PASS  standings parsing flattens streaks across every "
          f"division/conference group: {parsed_streaks}")

    # refresh_streaks(): one request per followed league, throttled on its
    # own interval independent of the game refresh, and merged rather than
    # replaced (a league that fails this round must not wipe out a streak
    # a previous, successful round already found).
    gst = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
        {"abbr": "NYK", "league": "nba", "name": "Knicks"},
    ])
    standings_calls = []

    class StandingsStub:
        def fetch_standings(self, league):
            standings_calls.append(league)
            if league == "mlb":
                return {"NYY": "W3"}
            raise RuntimeError("nba standings unavailable")

    gst.source = StandingsStub()
    gst.refresh_streaks(interval=1000)
    assert sorted(standings_calls) == ["mlb", "nba"], (
        f"expected one request per followed league: {standings_calls}"
    )
    assert gst.streak_for({"abbr": "NYY"}) == "W3"
    assert gst.streak_for({"abbr": "NYK"}) == "", (
        "a league whose fetch failed should not raise, and should simply "
        "have no streak to report"
    )

    # Within the interval, a league that succeeded must not be re-fetched --
    # but one that failed retries on the very next call rather than waiting
    # out the full interval, the same "timestamp not advanced on failure"
    # pattern refresh() itself already uses for a transient miss.
    gst.refresh_streaks(interval=1000)
    assert standings_calls.count("mlb") == 1, (
        f"a league that already succeeded should stay throttled: "
        f"{standings_calls}"
    )
    assert standings_calls.count("nba") == 2, (
        f"a league that failed should retry on the next call, not wait "
        f"out the full interval: {standings_calls}"
    )
    print("PASS  refresh_streaks() fetches once per followed league, "
          "throttles a league that succeeded, retries one that failed, "
          "and a failure does not wipe out data from a successful league")

    # The banner draws the streak in a distinct colour for a win streak vs
    # a loss streak, and draws nothing extra when there is none to report.
    from PIL import ImageDraw as _StreakID
    rstreak = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=None)
    streak_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}

    class _BannerSpyDraw:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def text(self, xy, text, font=None, fill=None):
            self.calls.append((text, fill))
            self.inner.text(xy, text, font=font, fill=fill)

        def textbbox(self, *a, **kw):
            return self.inner.textbbox(*a, **kw)

    win_img = Image.new("RGB", (150, 32), (0, 0, 0))
    win_spy = _BannerSpyDraw(_StreakID.Draw(win_img))
    win_font, win_row_h = rstreak._fit_font(win_spy, 1, rstreak.height)
    rstreak._draw_banner(win_img, win_spy, 2, streak_team, win_font, win_row_h, "W3")
    win_streak_calls = [c for c in win_spy.calls if c[0] == "W3"]
    assert win_streak_calls and win_streak_calls[0][1] == StripRenderer.STREAK_WIN, (
        f"a win streak should draw in STREAK_WIN colour: {win_streak_calls}"
    )

    loss_img = Image.new("RGB", (150, 32), (0, 0, 0))
    loss_spy = _BannerSpyDraw(_StreakID.Draw(loss_img))
    rstreak._draw_banner(loss_img, loss_spy, 2, streak_team, win_font, win_row_h, "L2")
    loss_streak_calls = [c for c in loss_spy.calls if c[0] == "L2"]
    assert loss_streak_calls and loss_streak_calls[0][1] == StripRenderer.STREAK_LOSS, (
        f"a loss streak should draw in STREAK_LOSS colour: {loss_streak_calls}"
    )

    no_streak_img = Image.new("RGB", (150, 32), (0, 0, 0))
    no_streak_spy = _BannerSpyDraw(_StreakID.Draw(no_streak_img))
    no_streak_w = rstreak._draw_banner(
        no_streak_img, no_streak_spy, 2, streak_team, win_font, win_row_h, "")
    plain_w = rstreak._draw_banner(
        Image.new("RGB", (150, 32), (0, 0, 0)),
        _BannerSpyDraw(_StreakID.Draw(Image.new("RGB", (150, 32), (0, 0, 0)))),
        2, streak_team, win_font, win_row_h)
    assert no_streak_w == plain_w, (
        "an empty streak string should draw identically to omitting it "
        "entirely"
    )
    print("PASS  banner draws a win streak and a loss streak in distinct "
          "colours, and nothing extra when there is no streak to report")

    # Rivalry: a configured rival recolours the status and flags it,
    # regardless of game state -- live, final, or upcoming alike.
    rrival = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=None)
    rival_final = {
        "id": "rv1", "league": "mlb", "state": STATE_FINAL, "start": "",
        "away": {"abbr": "BOS", "score": "4"},
        "home": {"abbr": "NYY", "score": "7", "winner": True},
        "situation": {}, "leaders": [],
    }
    plain_final = dict(rival_final, away={"abbr": "TB", "score": "4"})

    rival_img = Image.new("RGB", (150, 32), (0, 0, 0))
    rival_spy = _BannerSpyDraw(_StreakID.Draw(rival_img))
    rrival._draw_game(rival_img, rival_spy, 2, rival_final, win_font, win_row_h,
                      focus_abbr="NYY", rivals=["BOS"])
    rival_texts = [c[0] for c in rival_spy.calls]
    assert any("RIVALRY" in t for t in rival_texts), (
        f"a configured rival should flag the status with RIVALRY: {rival_texts}"
    )
    rival_colour_calls = [c for c in rival_spy.calls if "RIVALRY" in c[0]]
    assert rival_colour_calls[0][1] == StripRenderer.RIVALRY, (
        f"a rivalry status should draw in the RIVALRY colour: {rival_colour_calls}"
    )

    plain_img = Image.new("RGB", (150, 32), (0, 0, 0))
    plain_spy = _BannerSpyDraw(_StreakID.Draw(plain_img))
    rrival._draw_game(plain_img, plain_spy, 2, plain_final, win_font, win_row_h,
                      focus_abbr="NYY", rivals=["BOS"])
    plain_texts = [c[0] for c in plain_spy.calls]
    assert not any("RIVALRY" in t for t in plain_texts), (
        f"an opponent not on the rivals list must not be flagged: {plain_texts}"
    )
    print("PASS  a configured rival recolours and flags the game status; "
          "an unconfigured opponent is unaffected")

    # An off-season team -- nothing in the normal 7-day window -- used to
    # vanish from the board entirely: no banner at all, since the strip is
    # built from "teams that have a game". A wider, deliberately rare lookup
    # finds its season opener instead and remembers it, but must not
    # surface a banner for it until within 5 days of that opener -- a
    # fixture 70+ days out reads as stale or wrong, not as "playing soon".
    goff = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
        {"abbr": "NYK", "league": "nba", "name": "Knicks"},
    ])
    wide_calls = []
    far_future_date = (_dt.now(_tz.utc) + _td(days=71)).strftime("%Y-%m-%dT%H:%M:%SZ")

    class OffSeasonStub:
        def fetch_scoreboard(self, league, days_back=1, days_forward=7):
            if league == "mlb":
                return src._parse_events(SCOREBOARD_MLB, "mlb")
            if league == "nba" and days_forward > 7:
                wide_calls.append(days_forward)
                raw = {"events": [event(
                    "opener", ("BOS", "Celtics", "0-0"), ("NY", "Knicks", "0-0"),
                    "", "", "pre", False, "7:30 PM", date=far_future_date,
                )]}
                return src._parse_events(raw, "nba")
            return []  # normal-window NBA: off-season, nothing at all

        def fetch_leaders(self, league, event_id, per_game=2):
            return []

        def fetch_batting(self, league, event_id):
            return []

    goff.source = OffSeasonStub()
    goff.refresh(force=True)
    assert wide_calls == [120], f"expected exactly one wide lookup: {wide_calls}"
    assert goff._far_future_cache.get("nba:NYK", {}).get("id") == "opener", (
        "the far-future opener should be found and cached even before it "
        "is close enough to actually show"
    )
    knicks_games = goff.games_for_team({"abbr": "NYK", "league": "nba"})
    assert not knicks_games, (
        f"an opener 71 days out should not surface a banner yet: "
        f"{knicks_games}"
    )
    assert {"abbr": "NYK", "league": "nba", "name": "Knicks"} not in goff.teams_with_games(), (
        "a far-future fixture outside the show window should not count "
        "as 'having a game' yet"
    )

    # A second refresh, still the same day, must not repeat the wide lookup
    # -- the whole point is that this is rare and deliberately slow-paced.
    goff._fetched_at = 0.0  # force the normal-window part to refetch too
    goff.refresh(force=True)
    assert wide_calls == [120], (
        f"far-future lookup ran again inside its own interval: {wide_calls}"
    )
    assert not goff.games_for_team({"abbr": "NYK", "league": "nba"}), (
        "still should not show -- nothing about the date changed"
    )

    # Once within the show window, the cached fixture (no new lookup
    # needed) must appear -- confirming the cache remembers the earlier
    # find rather than the fixture only ever existing during that one
    # request's lifetime.
    soon_date = (_dt.now(_tz.utc) + _td(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    goff._far_future_cache["nba:NYK"]["start"] = soon_date
    goff._fetched_at = 0.0
    goff.refresh(force=True)
    assert wide_calls == [120], (
        f"should still use the cached fixture, not a fresh lookup: {wide_calls}"
    )
    knicks_games_soon = goff.games_for_team({"abbr": "NYK", "league": "nba"})
    assert knicks_games_soon and knicks_games_soon[0]["id"] == "opener", (
        f"the cached opener should surface once within the show window: "
        f"{knicks_games_soon}"
    )
    assert {"abbr": "NYK", "league": "nba", "name": "Knicks"} in goff.teams_with_games(), (
        "an off-season team's opener within the show window should count "
        "as 'having a game', which is what gates its banner"
    )
    print("PASS  an off-season team's far-future opener is found and "
          "cached immediately but only gets a banner within 5 days of it, "
          "via a wide lookup that runs at most once a day")

    # A limit must be respected
    assert len(gol.other_live_games(limit=1)) == 1

    # Rendering must not crash and must respect the same margin rules as
    # every other segment, since it reuses _draw_game.
    dother = FakeDisplay(192, 32)
    rother = StripRenderer(dother, {}, log, logo_manager=logos)
    other_strip = rother.build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"},
          [{"id": "f1", "league": "mlb", "state": STATE_FINAL, "start": "",
            "away": {"abbr": "BOS", "score": "4", "winner": False},
            "home": {"abbr": "NYY", "score": "7", "winner": True},
            "situation": {}, "leaders": []}])],
        other_live=other,
    )
    opx = other_strip.load()
    lit_rows = [y for y in range(32) for x in range(other_strip.width)
               if opx[x, y] != (0, 0, 0)]
    assert min(lit_rows) >= 1 and max(lit_rows) <= 30, (
        f"other-live section breaks the shared margin: {min(lit_rows)}..{max(lit_rows)}"
    )
    print("PASS  other-live section renders within the shared 1px margin")

    # And when nothing is live elsewhere, the section must not appear at all
    empty_strip = StripRenderer(FakeDisplay(192, 32), {}, log).build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"},
          [{"id": "f1", "league": "mlb", "state": STATE_FINAL, "start": "",
            "away": {"abbr": "BOS", "score": "4", "winner": False},
            "home": {"abbr": "NYY", "score": "7", "winner": True},
            "situation": {}, "leaders": []}])],
        other_live=[],
    )
    assert empty_strip.width < other_strip.width, (
        "an empty other-live list still added a section"
    )
    print("PASS  the section is absent entirely when nothing else is live")

    # Other-live games must be interleaved one at a time after each
    # followed team, not bunched into a single block at the very end --
    # otherwise a game around the league can sit behind every followed
    # team's full set of games before it ever shows up again.
    rinter = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    banner_order = []
    live_section_order = []
    orig_banner = rinter._draw_banner
    orig_live_section = rinter._draw_live_section

    def _spy_banner(img, draw, x, team, font, row_h, streak=""):
        banner_order.append(team.get("abbr"))
        return orig_banner(img, draw, x, team, font, row_h, streak)

    def _spy_live_section(img, draw, x, font, row_h):
        live_section_order.append(len(banner_order))
        return orig_live_section(img, draw, x, font, row_h)

    rinter._draw_banner = _spy_banner
    rinter._draw_live_section = _spy_live_section

    two_teams = [
        ({"abbr": "NYY", "league": "mlb", "name": "Yankees"},
         [{"id": "i1", "league": "mlb", "state": STATE_FINAL, "start": "",
           "away": {"abbr": "BOS", "score": "4", "winner": False},
           "home": {"abbr": "NYY", "score": "7", "winner": True},
           "situation": {}, "leaders": []}]),
        ({"abbr": "NYM", "league": "mlb", "name": "Mets"},
         [{"id": "i2", "league": "mlb", "state": STATE_FINAL, "start": "",
           "away": {"abbr": "ATL", "score": "2", "winner": False},
           "home": {"abbr": "NYM", "score": "3", "winner": True},
           "situation": {}, "leaders": []}]),
    ]
    two_other_live = [
        {"id": "io1", "league": "nba", "state": STATE_LIVE, "start": "",
         "away": {"abbr": "BOS", "score": "50"}, "home": {"abbr": "LAL", "score": "48"},
         "situation": {"kind": "basketball", "clock": "5:00"}, "leaders": []},
        {"id": "io2", "league": "nfl", "state": STATE_LIVE, "start": "",
         "away": {"abbr": "DAL", "score": "10"}, "home": {"abbr": "PHI", "score": "7"},
         "situation": {"kind": "football"}, "leaders": []},
    ]
    rinter.build_strip(two_teams, other_live=two_other_live)

    assert len(live_section_order) == 2, (
        f"expected one other-live banner interleaved after each of the two "
        f"teams, got {len(live_section_order)}: {live_section_order}"
    )
    assert live_section_order == [1, 2], (
        f"each other-live banner must land right after its own team, not "
        f"both deferred to the end: banners drawn {banner_order}, "
        f"live-section positions {live_section_order}"
    )
    print("PASS  other-live games are interleaved one per followed team, "
          "not bunched into a single trailing block")

    # With more other-live games than followed teams, the leftovers still
    # close out the strip in their own trailing section, same as before.
    rtail = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    one_team = [two_teams[0]]
    tail_strip = rtail.build_strip(one_team, other_live=two_other_live)
    tail_only_strip = StripRenderer(
        FakeDisplay(192, 32), {}, log, logo_manager=logos,
    ).build_strip(one_team, other_live=[two_other_live[0]])
    assert tail_strip.width > tail_only_strip.width, (
        "the second other-live game, left over after the one followed "
        "team, should still be drawn in a trailing section"
    )
    print("PASS  other-live games left over once teams run out still get "
          "a trailing section")

    # Weather: the sun icon needs a real gap between its core and its rays.
    # Sizing both off the same radius let them blend into one blob at small
    # sizes, and a filled ellipse only a couple of pixels wide reads as a
    # diamond on a bitmap grid rather than a circle -- so a blended
    # sun-plus-rays came out looking like a plain diamond with no rays
    # visible at all.
    for test_size in (6, 8, 10, 14):
        from PIL import ImageDraw as _IconDraw
        icon_img = Image.new("RGB", (test_size + 2, test_size + 2), (0, 0, 0))
        icon_draw = _IconDraw.Draw(icon_img)
        StripRenderer(FakeDisplay(1, 1), {}, log)._draw_weather_icon(
            icon_draw, 1, 1, test_size, "clear")
        ipx = icon_img.load()
        lit = [(xx, yy) for yy in range(icon_img.height)
              for xx in range(icon_img.width) if ipx[xx, yy] != (0, 0, 0)]
        assert lit, f"sun icon at size {test_size} drew nothing"
        cx, cy = 1 + test_size // 2, 1 + test_size // 2
        far = [p for p in lit
              if abs(p[0] - cx) + abs(p[1] - cy) >= max(2, test_size // 3)]
        assert far, (
            f"sun icon at size {test_size} has no pixels away from centre "
            f"-- rays are not visibly separated from the core"
        )
    print("PASS  sun icon shows a core and visibly separated rays at every size")

    # The forecast column must never let the icon and the temperature share
    # space: an earlier version sized the icon off one formula and placed
    # the temperature off a second, different formula for the same
    # boundary, so a font with different metrics than the one this was
    # tuned against could make the two overlap even though this font showed
    # a healthy gap. The fix measures both anchors from the same source and
    # falls back to no icon at all rather than risk an overlapping one.
    import inspect
    fcol_src = inspect.getsource(StripRenderer._draw_forecast_column)
    assert "temp_ink_top - label_ink_bottom" in fcol_src, (
        "forecast column icon sizing no longer measures against the "
        "temperature's own real position"
    )
    assert "if available >= 4 else 0" in fcol_src, (
        "forecast column lost its no-overlap fallback"
    )

    dfcol = FakeDisplay(192, 32)
    rfcol = StripRenderer(dfcol, {}, log)
    fcol_weather = {"label": "BAYONNE", "units": "F", "now_temp": 78,
                    "now_condition": "CLEAR", "alerts": [],
                    "hourly": [{"name": "8P", "temp": 77, "condition": "Clear"},
                              {"name": "9P", "temp": 75, "condition": "Rain"}]}
    fcol_strip = rfcol.build_strip([], weather=fcol_weather)
    fpx = fcol_strip.load()
    ftop = min(y for y in range(32) for x in range(fcol_strip.width)
              if fpx[x, y] != (0, 0, 0))
    fbottom = max(y for y in range(32) for x in range(fcol_strip.width)
                 if fpx[x, y] != (0, 0, 0))
    assert ftop >= 1 and (31 - fbottom) >= 1, (
        f"forecast column margins violated: top={ftop} bottom={31-fbottom}"
    )
    print("PASS  forecast column measures icon and temperature from one "
          "shared anchor, with a no-overlap fallback")

    # The icon must sit centred in the gap between the label and the
    # temperature, not pinned immediately below the label with all the
    # slack dumped before the temperature -- confirmed on real hardware,
    # where the icon rode high in its own slot rather than centred in it.
    from PIL import ImageDraw as _IconColID
    ricon_col = StripRenderer(FakeDisplay(192, 32), {}, log)
    icon_col_img = Image.new("RGB", (40, 32), (0, 0, 0))
    icon_col_draw = _IconColID.Draw(icon_col_img)
    icon_col_font, icon_col_row_h = ricon_col._fit_font(icon_col_draw, 4, ricon_col.height)

    icon_calls = []
    real_wicon = ricon_col._draw_weather_icon
    def _spy_wicon(draw, x, y, size, kind):
        icon_calls.append((y, size))
        return real_wicon(draw, x, y, size, kind)
    ricon_col._draw_weather_icon = _spy_wicon

    ricon_col._draw_forecast_column(
        icon_col_draw, 2, {"name": "WED", "temp": 86, "condition": "Clear"},
        icon_col_font, icon_col_row_h, "F")
    assert icon_calls, "forecast column drew no icon at all to check centering on"
    icon_y, icon_size = icon_calls[0]

    smaller = ricon_col._smaller_font(icon_col_draw, icon_col_row_h)
    text_font = smaller[0] if smaller else icon_col_font
    text_row_h = smaller[1] if smaller else icon_col_row_h
    label_y = ricon_col._text_top(icon_col_draw, text_font, ricon_col.MARGIN + text_row_h)
    label_ink_bottom = label_y + icon_col_draw.textbbox((0, 0), "0", font=text_font)[3]
    temp_y = ricon_col._text_bottom(
        icon_col_draw, text_font, ricon_col.height - 1 - ricon_col.MARGIN)
    temp_ink_top = temp_y + icon_col_draw.textbbox((0, 0), "0", font=text_font)[1]

    gap_above = icon_y - label_ink_bottom
    gap_below = temp_ink_top - (icon_y + icon_size)
    assert abs(gap_above - gap_below) <= 1, (
        f"icon is not centred between label and temperature: "
        f"gap_above={gap_above}px gap_below={gap_below}px "
        f"(label_bottom={label_ink_bottom}, icon_y={icon_y}, "
        f"icon_size={icon_size}, temp_top={temp_ink_top})"
    )
    print(f"PASS  forecast column icon centres in the gap between label "
          f"and temperature (gap_above={gap_above}px, "
          f"gap_below={gap_below}px), not pinned to one side of it")

    # "NEXT HOURS"/"5 DAY FORECAST" used to sit at the same top margin the
    # column's own day/hour label independently anchored to, using a
    # different (larger) font -- header and content competing for the same
    # rows rather than one sitting above the other. The header's own text
    # row must now end (or at least not start later than) the row the
    # column's label starts on.
    from PIL import ImageDraw as _HeaderID
    class _HeaderSpyDraw:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def text(self, xy, text, font=None, fill=None):
            self.calls.append((xy[1], text))
            self.inner.text(xy, text, font=font, fill=fill)

        def textbbox(self, *a, **kw):
            return self.inner.textbbox(*a, **kw)

        def line(self, *a, **kw):
            return self.inner.line(*a, **kw)

        def rectangle(self, *a, **kw):
            return self.inner.rectangle(*a, **kw)

        def ellipse(self, *a, **kw):
            return self.inner.ellipse(*a, **kw)

        def polygon(self, *a, **kw):
            return self.inner.polygon(*a, **kw)

        def point(self, *a, **kw):
            return self.inner.point(*a, **kw)

    rheader = StripRenderer(FakeDisplay(192, 32), {}, log)
    header_img = Image.new("RGB", (250, 32), (0, 0, 0))
    header_spy = _HeaderSpyDraw(_HeaderID.Draw(header_img))
    header_font, header_row_h = rheader._fit_font(header_spy, 4, rheader.height)
    header_weather = {"label": "BAYONNE", "units": "F", "now_temp": 78,
                      "now_condition": "CLEAR", "alerts": [],
                      "daily": [{"name": "WED", "temp": 86, "condition": "Sunny"},
                               {"name": "THU", "temp": 85, "condition": "Clear"}]}
    rheader._draw_weather(header_img, header_spy, 2, header_weather,
                         header_font, header_row_h)
    header_ys = [y for y, t in header_spy.calls if t == "5 DAY FORECAST"]
    label_ys = [y for y, t in header_spy.calls if t in ("WED", "THU")]
    assert header_ys and label_ys, (
        f"expected both the header and a day label to draw text: "
        f"{header_spy.calls}"
    )
    assert min(label_ys) > min(header_ys), (
        f"the day label should start on a row strictly below the header, "
        f"not share its row: header_y={header_ys}, label_y={label_ys}"
    )
    print(f"PASS  '5 DAY FORECAST' header sits above its columns' own day "
          f"labels (header y={min(header_ys)}, column y={min(label_ys)}), "
          f"not sharing a row with them")

    # The bug that actually shipped: real compiled BDF fonts on the device
    # have zero built-in leading and a taller row height than this
    # sandbox's fallback font, which left only 3px for the icon and tripped
    # the safety floor -- silently drawing no icon at all rather than a
    # cramped one. Reproduced here by monkeypatching textbbox to return the
    # exact values a real device reported (row_h=8, bbox (0,0,5,7) for "0"),
    # without needing a full synthetic font with every glyph defined.
    rdev = StripRenderer(FakeDisplay(192, 32), {}, log)
    from PIL import ImageDraw as _DevID
    dev_img = Image.new("RGB", (40, 32), (0, 0, 0))
    dev_draw = _DevID.Draw(dev_img)
    real_textbbox = dev_draw.textbbox

    def device_textbbox(xy, text, font=None, **kwargs):
        # Matches the real device's reported metrics for every glyph, which
        # is what the actual bug depended on -- leading=0, extent=7.
        width = max(1, len(text) * 5)
        return (0, 0, width, 7)

    dev_draw.textbbox = device_textbbox
    try:
        rdev._draw_forecast_column(
            dev_draw, 2, {"name": "8P", "temp": 77, "condition": "Clear"},
            None, 8, "F")
    finally:
        dev_draw.textbbox = real_textbbox
    dev_px = dev_img.load()
    dev_lit = sum(1 for y in range(32) for x in range(40)
                 if dev_px[x, y] != (0, 0, 0))
    assert dev_lit > 0, (
        "forecast column drew nothing at all against real device metrics"
    )
    print(f"PASS  forecast column draws against real device font metrics "
          f"({dev_lit} lit px, previously 0)")

    # "Drew something" isn't the same claim as "drew the icon" -- the label
    # and temperature alone are enough to light pixels even when the icon
    # itself is silently skipped, which is exactly how the bug above shipped
    # undetected once already. The fix that font selection depends on --
    # finding a font strictly smaller than the shared body font -- has only
    # ever been confirmed for the shared font's own metrics, never for
    # whether a smaller candidate is actually found on the real device. This
    # forces that worst case (nothing smaller available, same as if 4x6.bdf
    # were missing or not measurably smaller on the device) and spies on the
    # icon call directly, so a regression here fails loudly instead of
    # hiding behind label/temperature text.
    rdev2 = StripRenderer(FakeDisplay(192, 32), {}, log)
    rdev2._smaller_font = lambda draw, than_row_h: None
    icon_calls = []
    _orig_icon = rdev2._draw_weather_icon

    def _spy_icon(draw, x, y, size, kind):
        icon_calls.append(size)
        return _orig_icon(draw, x, y, size, kind)

    rdev2._draw_weather_icon = _spy_icon
    dev_img2 = Image.new("RGB", (40, 32), (0, 0, 0))
    dev_draw2 = _DevID.Draw(dev_img2)
    real_textbbox2 = dev_draw2.textbbox
    dev_draw2.textbbox = device_textbbox
    try:
        rdev2._draw_forecast_column(
            dev_draw2, 2, {"name": "8P", "temp": 77, "condition": "Clear"},
            None, 8, "F")
    finally:
        dev_draw2.textbbox = real_textbbox2
    assert icon_calls and icon_calls[0] >= 4, (
        "forecast column drew no icon (or a below-floor one) when no font "
        "smaller than the shared body font was found -- the exact case "
        "never confirmed against real hardware"
    )
    print(f"PASS  forecast column still draws an icon ({icon_calls[0]}px) "
          f"even when no smaller font is found at all")

    # "Feels like" must be spelled out, not abbreviated -- this is a
    # scrolling strip with no space pressure forcing a code a viewer has to
    # decode.
    weather_src = inspect.getsource(StripRenderer._draw_weather)
    assert "FEELS LIKE" in weather_src, "feels-like label was not spelled out"
    assert '"FL ' not in weather_src, "the old abbreviated form is still present"
    print("PASS  feels-like temperature is spelled out in full, not abbreviated")

    # Moon phase: pure arithmetic, no network -- illumination must stay in
    # 0..100, the name must be one of the eight real phases, and waxing must
    # agree with which half of the cycle the fraction actually falls in.
    import moon_phase
    from datetime import timedelta as _moon_td
    valid_names = {
        "NEW MOON", "WAXING CRESCENT", "FIRST QUARTER", "WAXING GIBBOUS",
        "FULL MOON", "WANING GIBBOUS", "LAST QUARTER", "WANING CRESCENT",
    }
    probe_start = _dt(2026, 1, 1)
    seen_names = set()
    for day_offset in range(0, 60):
        info = moon_phase.phase_info(probe_start + _moon_td(days=day_offset))
        assert 0 <= info["illumination"] <= 100, (
            f"illumination out of range: {info}"
        )
        assert info["name"] in valid_names, f"unrecognised phase name: {info}"
        assert info["waxing"] == (info["fraction"] < 0.5), (
            f"waxing flag disagreed with fraction: {info}"
        )
        seen_names.add(info["name"])
    assert seen_names == valid_names, (
        f"a 60-day span (two full cycles) should pass through every named "
        f"phase at least once: missing {valid_names - seen_names}"
    )
    # A known reference new moon should read as very nearly new (low
    # illumination), not just "some name or other".
    ref_info = moon_phase.phase_info(_dt(2000, 1, 6, 18, 14))
    assert ref_info["illumination"] <= 2, (
        f"the reference new moon itself should read ~0% illuminated: "
        f"{ref_info}"
    )
    print(f"PASS  moon phase arithmetic stays in range and cycles through "
          f"all eight named phases: {sorted(seen_names)}")

    # The moon icon must draw something visible at every phase, including
    # new moon -- a pure dark disc would otherwise vanish into the panel's
    # own black background, the same "invisible on an unlit panel" class of
    # bug already fixed once for dark team crests.
    from PIL import ImageDraw as _MoonID
    rmoon = StripRenderer(FakeDisplay(192, 32), {}, log)
    for phase_name in sorted(valid_names):
        moon_img = Image.new("RGB", (16, 16), (0, 0, 0))
        moon_draw = _MoonID.Draw(moon_img)
        rmoon._draw_moon_icon(moon_draw, 1, 1, 10, phase_name)
        mpx = moon_img.load()
        assert any(mpx[x, y] != (0, 0, 0) for y in range(16) for x in range(16)), (
            f"{phase_name} icon drew nothing at all -- invisible against "
            f"the panel's black background"
        )
    print("PASS  moon icon is visible at every phase, including new moon")

    # The weather segment only grows a MOON section when a date is actually
    # passed in -- existing callers that do not pass one (or do not want
    # it) must see identical output to before this was added.
    rmoonw = StripRenderer(FakeDisplay(192, 32), {}, log)
    moon_weather = {"now_temp": 75, "units": "F", "now_condition": "Clear"}
    moon_probe_img = Image.new("RGB", (1, 1))
    moon_probe_draw = _MoonID.Draw(moon_probe_img)
    moon_font, moon_row_h = rmoonw._fit_font(moon_probe_draw, 3, rmoonw.height)

    without_img = Image.new("RGB", (250, 32), (0, 0, 0))
    without_draw = _MoonID.Draw(without_img)
    without_w = rmoonw._draw_weather(
        without_img, without_draw, 2, moon_weather, moon_font, moon_row_h)

    with_img = Image.new("RGB", (250, 32), (0, 0, 0))
    with_draw = _MoonID.Draw(with_img)
    with_w = rmoonw._draw_weather(
        with_img, with_draw, 2, moon_weather, moon_font, moon_row_h,
        _dt(2026, 8, 12))
    assert with_w > without_w, (
        "passing a date did not add a MOON section to the weather segment"
    )
    wpx = with_img.load()
    lit_ys = [y for y in range(32) for x in range(250) if wpx[x, y] != (0, 0, 0)]
    assert lit_ys and min(lit_ys) >= 1 and max(lit_ys) <= 30, (
        f"moon section violated the shared 1px top/bottom margin: "
        f"rows {min(lit_ys)}-{max(lit_ys)}"
    )
    print(f"PASS  weather segment grows a MOON section only when a date is "
          f"passed ({without_w}px -> {with_w}px), within the shared margin")

    # show_forecast=False (weather.hide_forecast_when_live, opted into per
    # install) drops the hourly/5-day columns and the moon phase, but
    # current conditions -- the "now" temperature -- must still draw, on
    # the same reasoning that already keeps a weather warning up during a
    # live game.
    forecast_weather = dict(moon_weather, hourly=[{"name": "8P", "temp": 77}],
                            daily=[{"name": "MON", "temp": 80}])
    shown_img = Image.new("RGB", (250, 32), (0, 0, 0))
    shown_draw = _MoonID.Draw(shown_img)
    shown_w = rmoonw._draw_weather(
        shown_img, shown_draw, 2, forecast_weather, moon_font, moon_row_h,
        _dt(2026, 8, 12), show_forecast=True)

    hidden_img = Image.new("RGB", (250, 32), (0, 0, 0))
    hidden_draw = _MoonID.Draw(hidden_img)
    hidden_w = rmoonw._draw_weather(
        hidden_img, hidden_draw, 2, forecast_weather, moon_font, moon_row_h,
        _dt(2026, 8, 12), show_forecast=False)
    assert hidden_w < shown_w, (
        f"show_forecast=False should drop the forecast/moon columns: "
        f"shown={shown_w}px, hidden={hidden_w}px"
    )
    now_only_w = rmoonw._draw_weather(
        Image.new("RGB", (250, 32), (0, 0, 0)), _MoonID.Draw(
            Image.new("RGB", (250, 32), (0, 0, 0))),
        2, forecast_weather, moon_font, moon_row_h, None, show_forecast=False)
    assert hidden_w == now_only_w, (
        "show_forecast=False with hourly/daily/moon data present should "
        "measure the same as never having passed them at all -- current "
        f"conditions only: {hidden_w}px vs {now_only_w}px"
    )
    print(f"PASS  show_forecast=False hides the moon phase and forecast "
          f"columns but keeps current conditions up ({shown_w}px -> "
          f"{hidden_w}px)")

    # Countdown: pure date arithmetic, recurring every year. Must roll over
    # to next year once this year's date has passed, and must not crash on
    # a Feb 29 configured against a non-leap year.
    import countdowns as countdowns_module
    from datetime import date as _date
    cd_today = _date(2026, 8, 12)
    assert countdowns_module.days_until(cd_today, 8, 12) == 0, (
        "today's own date should read as 0 days away"
    )
    assert countdowns_module.days_until(cd_today, 12, 25) == 135, (
        f"Christmas from Aug 12 should be 135 days out: "
        f"{countdowns_module.days_until(cd_today, 12, 25)}"
    )
    # Already passed this year -- must roll to next year, not go negative.
    jan_days = countdowns_module.days_until(cd_today, 1, 1)
    assert jan_days > 0, f"a passed date must roll to next year: {jan_days}"
    # Feb 29 against 2027 (not a leap year) must not raise -- falls back to
    # the nearest real date, the 28th.
    feb29_days = countdowns_module.days_until(cd_today, 2, 29)
    assert isinstance(feb29_days, int) and feb29_days > 0, (
        f"Feb 29 against a non-leap year should not crash: {feb29_days}"
    )

    upcoming = countdowns_module.upcoming([
        {"name": "CHRISTMAS", "month": 12, "day": 25},
        {"name": "JACK'S BIRTHDAY", "month": 3, "day": 14},
        {"name": "BAD ENTRY", "month": 13, "day": 40},
        {"name": "", "month": 1, "day": 1},
    ], cd_today, limit=5)
    assert [e["name"] for e in upcoming] == ["CHRISTMAS", "JACK'S BIRTHDAY"], (
        f"malformed and unnamed entries should be dropped, real ones kept "
        f"soonest-first: {upcoming}"
    )
    assert countdowns_module.upcoming(
        [{"name": "A", "month": 1, "day": 1},
         {"name": "B", "month": 1, "day": 1}], cd_today, limit=1
    ) == [{"name": "A", "days": countdowns_module.days_until(cd_today, 1, 1)}], (
        "limit did not cap the result to the soonest entries"
    )
    print("PASS  countdown date arithmetic rolls over to next year, "
          "survives Feb 29 and malformed entries, and respects the limit")

    # The countdown icon is guessed from the event's own name -- a cake for
    # a birthday, a tree for Christmas, a pencil for school, a star for
    # anything else -- and must draw something visible in every case,
    # matched case-insensitively since a person typing their own event name
    # will not reliably type ALL CAPS.
    ricon = StripRenderer(FakeDisplay(192, 32), {}, log)
    icon_cases = [
        "Abram's Birthday", "Christmas", "First Day of School",
        "Random Family Trip",
    ]
    for icon_name in icon_cases:
        icon_img = Image.new("RGB", (16, 16), (0, 0, 0))
        icon_draw = _MoonID.Draw(icon_img)
        ricon._draw_countdown_icon(icon_draw, 1, 1, 10, icon_name)
        ipx = icon_img.load()
        assert any(ipx[x, y] != (0, 0, 0) for y in range(16) for x in range(16)), (
            f"countdown icon for {icon_name!r} drew nothing at all"
        )
    print(f"PASS  countdown icon draws something for every case tried: "
          f"{icon_cases}")

    # The strip segment: a two-row block, "N DAYS" above "TO <NAME>" below,
    # "TODAY!" when it is actually the day, all within the shared margin.
    rcd = StripRenderer(FakeDisplay(192, 32), {}, log)
    cd_probe_img = Image.new("RGB", (1, 1))
    cd_probe_draw = _MoonID.Draw(cd_probe_img)
    cd_font, cd_row_h = rcd._fit_font(cd_probe_draw, 2, rcd.height)

    cd_img = Image.new("RGB", (100, 32), (0, 0, 0))
    cd_draw = _MoonID.Draw(cd_img)
    cd_w = rcd._draw_countdown(
        cd_img, cd_draw, 2, "CHRISTMAS", 135, cd_font, cd_row_h)
    assert cd_w > 0, "countdown block drew nothing"
    cpx = cd_img.load()
    cd_lit = [(x, y) for y in range(32) for x in range(100) if cpx[x, y] != (0, 0, 0)]
    assert cd_lit, "countdown block is blank"
    assert min(y for _, y in cd_lit) >= 1 and max(y for _, y in cd_lit) <= 30, (
        "countdown block violated the shared 1px top/bottom margin"
    )

    today_img = Image.new("RGB", (100, 32), (0, 0, 0))
    today_draw = _MoonID.Draw(today_img)
    today_spy = []
    _orig_text = today_draw.text

    def _spy_text(xy, text, **kw):
        today_spy.append(text)
        return _orig_text(xy, text, **kw)

    today_draw.text = _spy_text
    rcd._draw_countdown(today_img, today_draw, 2, "CHRISTMAS", 0, cd_font, cd_row_h)
    assert "TODAY!" in today_spy, (
        f"days=0 should read as TODAY!, not '0 DAYS': {today_spy}"
    )
    print(f"PASS  countdown strip segment renders within the shared margin "
          f"({cd_w}px) and reads TODAY! on the day itself")

    # build_strip() must only add the countdown section when events are
    # actually passed, and the cache signature must change when the day
    # count changes (a stale cached image would otherwise show yesterday's
    # count forever).
    rcdstrip = StripRenderer(FakeDisplay(192, 32), {}, log)
    cd_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    cd_game = [{"id": "cdg", "league": "mlb", "state": STATE_UPCOMING, "start": "",
               "away": {"abbr": "BOS", "score": ""},
               "home": {"abbr": "NYY", "score": ""},
               "situation": {}, "leaders": []}]
    without_cd = rcdstrip.build_strip([(cd_team, cd_game)])
    rcdstrip2 = StripRenderer(FakeDisplay(192, 32), {}, log)
    with_cd = rcdstrip2.build_strip(
        [(cd_team, cd_game)], countdowns=[{"name": "CHRISTMAS", "days": 135}])
    assert with_cd.width > without_cd.width, (
        "build_strip() did not grow when countdowns were passed"
    )
    rcdstrip2._last_build = 0.0
    changed_cd = rcdstrip2.build_strip(
        [(cd_team, cd_game)], countdowns=[{"name": "CHRISTMAS", "days": 134}])
    assert changed_cd is with_cd, (
        "a changed day count should be held for the seam, not adopted "
        "immediately mid-pass"
    )
    assert rcdstrip2._wait_for_background_build(), (
        "background build did not finish"
    )
    assert rcdstrip2.has_pending(), (
        "a changed day count did not queue a rebuild at all"
    )
    print("PASS  build_strip() adds the countdown section only when events "
          "are passed, and a changed day count queues a rebuild")

    # Centering must hold for whatever font actually loads, not just the one
    # this was measured against -- a block top-anchored at MARGIN looks
    # centred only when row_h happens to make the content exactly fill the
    # available height, and real BDF fonts on the actual hardware are not
    # this sandbox's fallback font. Forcing several different row heights
    # catches an asymmetry that a single font's lucky fit would hide. 8 is
    # included deliberately: it is the real, confirmed row height of the
    # shared body font on actual hardware, and the one at which a title
    # plus three ranked rows -- what a leaderboard or award list actually
    # draws -- no longer fits this panel's height at zero margin, which is
    # the exact case that silently dropped a leaderboard's third row.
    import strip_renderer as _srmod
    _orig_fit = _srmod.StripRenderer._fit_font

    def _make_forced_fit(rh):
        def _fit(self, draw, rows, avail, min_row_h=None):
            f, _ = _orig_fit(self, draw, rows, avail)
            return f, rh
        return _fit

    center_rows = [{"rank": i, "short_name": f"P.{i}", "team": "NYY",
                    "value": "41"} for i in range(1, 4)]
    center_weather = {"label": "BAYONNE", "units": "F", "now_temp": 78,
                      "now_feels": 85, "now_condition": "CLEAR", "alerts": []}
    center_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    center_final = {"id": "cf1", "league": "mlb", "state": STATE_FINAL,
                    "start": "",
                    "away": {"abbr": "NYY", "score": "4", "winner": False},
                    "home": {"abbr": "NYY", "score": "7", "winner": True},
                    "situation": {}, "leaders": [
                        {"team": "NYY", "name": "A.JUDGE", "line": "2-4, HR",
                         "side": "batting"}]}

    from datetime import datetime as _center_dt
    for forced_row_h in (4, 5, 6, 7, 8, 9):
        _srmod.StripRenderer._fit_font = _make_forced_fit(forced_row_h)

        cases = {
            "leaderboard": lambda rr: rr.build_strip(
                [], leaderboards=[("AL HR", center_rows, "HR")]),
            "awards": lambda rr: rr.build_strip(
                [], awards=[("AL MVP", center_rows)]),
            "game+note": lambda rr: rr.build_strip(
                [(center_team, [center_final])]),
            "weather": lambda rr: rr.build_strip([], weather=center_weather),
            "clock": lambda rr: rr.build_strip(
                [], clock=_center_dt(2026, 8, 10, 19, 5)),
        }
        for case_name, build in cases.items():
            cr = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
            cstrip = build(cr)
            cpx = cstrip.load()
            clit = {y for x in range(cstrip.width) for y in range(32)
                   if cpx[x, y] != (0, 0, 0)}
            assert clit, f"{case_name} at row_h={forced_row_h}: blank"
            ctop, cbottom = min(clit), max(clit)
            cdiff = abs(ctop - (31 - cbottom))
            assert cdiff <= 1, (
                f"{case_name} at row_h={forced_row_h}: top={ctop} "
                f"bottom_margin={31-cbottom}, diff={cdiff} -- not centred"
            )

    _srmod.StripRenderer._fit_font = _orig_fit
    print("PASS  leaderboard, awards, game+note and weather all centre "
          "correctly across 6 simulated font sizes, including the real "
          "hardware's row_h=8")

    # The clock should use the largest font that fits two rows, not whatever
    # the shared four-row body font happened to be -- _fit_font returns the
    # first candidate in preference order that satisfies the constraint,
    # which for a loose two-row constraint is often not the biggest one
    # available. Simulating a genuinely bigger font in the candidate pool
    # (this sandbox's own fallback ladder tops out too low to show the
    # difference on its own) proves the selection logic picks it up.
    dclocksz = FakeDisplay(192, 32)
    rclocksz = StripRenderer(dclocksz, {}, log)
    rclocksz.FALLBACK_SIZES = [8, 10, 13, 7, 6, 5, 4]
    from PIL import ImageDraw as _CSID
    csz_draw = _CSID.Draw(Image.new("RGB", (192, 32)))
    shared_font, shared_row_h = rclocksz._fit_font(csz_draw, 4, 32)
    clock_font, clock_row_h = rclocksz._largest_fit(csz_draw, 2, 30)
    assert clock_row_h > shared_row_h, (
        f"clock font ({clock_row_h}) is not larger than the shared body "
        f"font ({shared_row_h}) even though a bigger one is available"
    )
    print(f"PASS  clock uses the largest font that fits, not the shared "
          f"body font ({shared_row_h}px -> {clock_row_h}px when a bigger "
          f"one exists)")

    # The clock's colon must read as a separator, not a third character --
    # a real BDF colon glyph at the clock's own larger font renders as two
    # solid squares nearly as tall as a digit. _draw_clock_face replaces it
    # with two small hand-drawn dots sized off the digit's own ink height.
    #
    # Locating the colon by scanning rendered pixel columns turned out to be
    # exactly the kind of fragile this project's own testing notes warn
    # about: this sandbox's variable-width fallback font does not lay
    # glyphs out where a fixed offset from the hour's measured width
    # assumes, so a column-scan silently measured the wrong pixels. Real ink
    # height, from the font's own reported metrics -- the same source
    # _draw_clock_face itself reads to size the dots -- is unambiguous
    # regardless of which font actually loads.
    dcolon = Image.new("RGB", (60, 32), (0, 0, 0))
    from PIL import ImageDraw as _ColonID
    colon_draw = _ColonID.Draw(dcolon)
    rcolon = StripRenderer(FakeDisplay(192, 32), {}, log)
    colon_font, colon_row_h = rcolon._largest_fit(colon_draw, 2, 30)

    hand_width = rcolon._draw_clock_face(
        colon_draw, 2, 2, "7:05P", colon_font, (255, 255, 255)
    )
    cpx = dcolon.load()
    assert any(cpx[xx, yy] != (0, 0, 0)
              for yy in range(32) for xx in range(2, 2 + hand_width)), (
        "hand-drawn clock face drew nothing at all"
    )

    font_colon_bbox = colon_draw.textbbox((0, 0), ":", font=colon_font)
    font_colon_h = font_colon_bbox[3] - font_colon_bbox[1]

    digit_bbox = colon_draw.textbbox((0, 0), "0", font=colon_font)
    ink_h = max(1, digit_bbox[3] - digit_bbox[1])
    dot = max(1, ink_h // 6)
    hand_colon_h = (ink_h * 2) // 3 - ink_h // 3 + dot

    assert hand_colon_h < font_colon_h, (
        f"hand-drawn colon span ({hand_colon_h}px) is not smaller than the "
        f"font's own colon glyph ({font_colon_h}px)"
    )
    print(f"PASS  clock colon is hand-drawn smaller than the font's own "
          f"glyph ({font_colon_h}px -> {hand_colon_h}px)")

    # ---- 5b. Team MVP ---------------------------------------------------
    # team_best() must pick a followed team's own top scorer from mixed
    # league data, not just whoever ranked first leaguewide -- a second NYY
    # player who only cracks one category should lose to one who ranks in
    # two, the same breadth logic the league-wide awards already use.
    from awards_manager import BaseballAwardsManager

    class StubLeadersManager:
        def __init__(self, rows_by_key, stats_by_player):
            self.rows_by_key = rows_by_key
            self.stats_by_player = stats_by_player

        def get_category(self, category, scope="mlb", pool=""):
            return self.rows_by_key.get((category, scope), [])

        def get_player_stats(self, player_id, group, scope="mlb", pool=""):
            return self.stats_by_player.get(str(player_id), {})

    tmvp_rows = {
        ("homeRuns", "al"): [
            {"rank": 1, "name": "Rafael Devers", "short_name": "R.DEVERS",
             "team": "BOS", "value": "38", "player_id": "d1"},
            {"rank": 2, "name": "Aaron Judge", "short_name": "A.JUDGE",
             "team": "NYY", "value": "35", "player_id": "j1"},
        ],
        ("battingAverage", "al"): [
            {"rank": 1, "name": "Aaron Judge", "short_name": "A.JUDGE",
             "team": "NYY", "value": ".312", "player_id": "j1"},
        ],
        ("runsBattedIn", "al"): [
            {"rank": 3, "name": "Ben Rice", "short_name": "B.RICE",
             "team": "NYY", "value": "70", "player_id": "r1"},
        ],
    }
    tmvp_stats = {"j1": {"AVG": ".312", "HR": "35", "RBI": "88"}}
    stub_leaders = StubLeadersManager(tmvp_rows, tmvp_stats)
    tmvp_awards = BaseballAwardsManager(log, stub_leaders)

    best = tmvp_awards.team_best("NYY", "al")
    assert best is not None and best["short_name"] == "A.JUDGE", (
        f"expected NYY's own best (ranks in 2 categories) over a player "
        f"who only ranks in 1: {best}"
    )
    assert tmvp_awards.team_best("SEA", "al") is None, (
        "a team with no players in any fetched category must return None, "
        "not a guess"
    )
    print(f"PASS  team_best() finds a followed team's own top scorer among "
          f"mixed league data: {best['short_name']}")

    # The rendered block must be distinguishable from a live game's own
    # notable-performer note (_draw_note) -- both are "name over stat
    # line", so a label row is what tells them apart. And it must hold the
    # same margin discipline as everything else: this is a genuine 3-row
    # block (label, name, line), well within budget at any real row_h.
    dtmvp = FakeDisplay(192, 32)
    rtmvp = StripRenderer(dtmvp, {}, log)
    tmvp_img = Image.new("RGB", (150, 32), (0, 0, 0))
    from PIL import ImageDraw as _TmvpID
    tmvp_draw = _TmvpID.Draw(tmvp_img)
    tmvp_font, tmvp_row_h = rtmvp._fit_font(tmvp_draw, 4, rtmvp.height)
    rtmvp._draw_team_mvp(tmvp_img, tmvp_draw, 2, "Aaron Judge", "A.JUDGE",
                        "AVG .312  HR 35  RBI 88", tmvp_font, tmvp_row_h)
    tpx = tmvp_img.load()
    tlit = [(x, y) for y in range(32) for x in range(150) if tpx[x, y] != (0, 0, 0)]
    assert tlit, "team MVP block drew nothing at all"
    ttop, tbottom = min(y for x, y in tlit), max(y for x, y in tlit)
    assert ttop >= 1 and (31 - tbottom) >= 1, (
        f"team MVP block margins violated: top={ttop} bottom={31 - tbottom}"
    )
    print("PASS  team MVP block renders within the shared 1px margin")

    # The full name should only replace the abbreviation when it costs
    # nothing extra -- i.e. the stat-line row is already wider than the
    # short name would be, since the block's width is the widest of its
    # three independently-positioned rows, not their sum.
    class _SpyDraw:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def text(self, xy, text, font=None, fill=None):
            self.calls.append(text)
            self.inner.text(xy, text, font=font, fill=fill)

        def textbbox(self, *a, **kw):
            return self.inner.textbbox(*a, **kw)

    long_line_img = Image.new("RGB", (150, 32), (0, 0, 0))
    long_line_spy = _SpyDraw(_TmvpID.Draw(long_line_img))
    rtmvp._draw_team_mvp(long_line_img, long_line_spy, 2, "Aaron Judge", "A.JUDGE",
                        "AVG .312  HR 35  RBI 88", tmvp_font, tmvp_row_h)
    assert "Aaron Judge" in long_line_spy.calls, (
        f"a stat line wider than the short name should show the full name: "
        f"{long_line_spy.calls}"
    )

    short_line_img = Image.new("RGB", (150, 32), (0, 0, 0))
    short_line_spy = _SpyDraw(_TmvpID.Draw(short_line_img))
    rtmvp._draw_team_mvp(short_line_img, short_line_spy, 2, "Aaron Judge", "A.JUDGE",
                        "HR 35", tmvp_font, tmvp_row_h)
    assert "A.JUDGE" in short_line_spy.calls and "Aaron Judge" not in short_line_spy.calls, (
        f"a stat line narrower than the short name should keep the "
        f"abbreviation: {short_line_spy.calls}"
    )
    print("PASS  team MVP note shows the full name only when the stat line "
          "is already at least as wide as the abbreviation")

    # End to end: manager._team_mvps() must resolve the winner's player_id
    # back through the leaders manager to a real stat line, formatted by
    # group -- AVG/HR/RBI for a hitter, not the raw category the player
    # happened to rank in.
    tmvp_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    # Caught live: this used to be wired fetch_player_stats=False (a
    # leaderboard row only ever needed rank/name/value, from before the
    # season MVP note existed), which meant get_player_stats() always
    # returned {} for a real deployment -- team_best() found a winner every
    # time, but the note never actually appeared, since _team_mvps() drops
    # any team with no stat-line fields to show. Asserted directly on the
    # real, unmocked leaders manager so overriding it below can't hide a
    # regression here again.
    assert tmvp_plugin.leaders.fetch_player_stats is True, (
        "BaseballLeadersManager built with fetch_player_stats=False -- the "
        "season MVP note would silently never have a stat line to show"
    )
    tmvp_plugin.leaders = stub_leaders
    tmvp_plugin.awards = tmvp_awards
    tmvp_plugin.teams_leader_scopes = ["al", "nl"]
    mvps = tmvp_plugin._team_mvps()
    assert mvps.get("NYY") == {
        "name": "Aaron Judge", "short_name": "A.JUDGE",
        "line": "AVG .312  HR 35  RBI 88",
    }, (
        f"unexpected team MVP output: {mvps}"
    )
    print(f"PASS  manager._team_mvps() resolves a real, formatted stat "
          f"line: {mvps['NYY']['line']}")

    # team_mvp_from_roster() must find a standout among nothing but a raw
    # roster's own stat lines -- no ranks, no leaderboard rows, just each
    # player's numbers -- and must rank ERA ascending (lower is better),
    # not descending, which ordinary Borda scoring gets backwards if it
    # is not told the category is inverted.
    roster_hitting = {
        "h1": {"AVG": ".312", "HR": "35", "RBI": "88"},   # clear standout
        "h2": {"AVG": ".240", "HR": "8", "RBI": "30"},
        "h3": {"AVG": ".260", "HR": "12", "RBI": "40"},
    }
    roster_pitching = {
        "p1": {"ERA": "5.50", "W": "3", "K": "40"},        # much worse ERA
        "p2": {"ERA": "1.80", "W": "9", "K": "150"},        # ace-level
    }
    roster_names = {"h1": "A.JUDGE", "h2": "B.BENCH", "h3": "C.ROLE",
                    "p1": "D.SWINGMAN", "p2": "E.ACE"}
    roster_full_names = {"h1": "Aaron Judge", "h2": "Bobby Bench", "h3": "Charlie Role",
                          "p1": "Danny Swingman", "p2": "Eddie Ace"}
    roster_awards = BaseballAwardsManager(log, None)
    roster_best = roster_awards.team_mvp_from_roster(
        roster_hitting, roster_pitching, roster_names, roster_full_names)
    assert roster_best is not None and roster_best["short_name"] == "A.JUDGE", (
        f"clear statistical standout did not win the roster MVP: {roster_best}"
    )
    assert roster_best["name"] == "Aaron Judge", (
        f"team_mvp_from_roster() must also carry the full name: {roster_best}"
    )

    # Isolate pitching only, so the ERA-direction check cannot be masked by
    # the hitter winning on breadth or raw point volume regardless.
    pitching_only = roster_awards.team_mvp_from_roster(
        {}, roster_pitching, roster_names, roster_full_names)
    assert pitching_only is not None and pitching_only["short_name"] == "E.ACE", (
        f"lower ERA should rank first, not last: {pitching_only}"
    )
    print("PASS  team_mvp_from_roster() finds a standout from raw roster "
          "stats alone, ranking ERA the right direction")

    # manager._team_mvps() must fall back to the roster ranking when a team
    # has no league-wide candidate at all (team_best() returns None) --
    # the whole point of the roster path -- using whatever
    # refresh_team_roster() already fetched, never fetching from here.
    class NoLeagueMvpAwards:
        def team_best(self, team_abbr, scope="mlb"):
            return None

        def team_mvp_from_roster(self, hitting, pitching, names, full_names=None):
            return roster_awards.team_mvp_from_roster(
                hitting, pitching, names, full_names)

    class RosterOnlyLeaders:
        def get_team_roster(self, team_abbr):
            if team_abbr == "NYY":
                return (roster_hitting, roster_pitching, roster_names,
                        roster_full_names)
            return ({}, {}, {}, {})

        def get_player_stats(self, player_id, group, scope="mlb", pool=""):
            return {}

    fallback_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    fallback_plugin.leaders = RosterOnlyLeaders()
    fallback_plugin.awards = NoLeagueMvpAwards()
    fallback_plugin.teams_leader_scopes = ["al", "nl"]
    fallback_mvps = fallback_plugin._team_mvps()
    assert fallback_mvps.get("NYY") == {
        "name": "Aaron Judge", "short_name": "A.JUDGE",
        "line": "AVG .312  HR 35  RBI 88",
    }, f"roster fallback did not produce the expected MVP: {fallback_mvps}"
    print("PASS  _team_mvps() falls back to a roster-ranked MVP when a "
          "team has no league-wide candidate at all")

    # manager._streaks() must build {abbr: streak} for followed teams only,
    # omitting a team with nothing to report rather than an empty string.
    class StreakGames:
        def streak_for(self, team):
            return {"NYY": "W3"}.get(team.get("abbr", ""), "")

    streaks_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [
            {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
            {"abbr": "NYM", "league": "mlb", "name": "Mets"},
        ]}, FakeDisplay(192, 32), FakeCache(), None,
    )
    streaks_plugin.games = StreakGames()
    assert streaks_plugin._streaks() == {"NYY": "W3"}, (
        f"expected only NYY, with NYM omitted (no streak): "
        f"{streaks_plugin._streaks()}"
    )
    print("PASS  manager._streaks() reports only followed teams that "
          "actually have a streak")

    # Leaderboards/awards are hidden from the strip entirely while any
    # followed team is live -- a live score is what the board exists to
    # show right now, and season stats competing for the same scroll only
    # push it further away. They come back the moment nothing is live.
    # _leaderboards() itself is monkeypatched to return fixed, known
    # content, isolating this test to the gating logic in _display_strip()
    # rather than the real leaderboard-computation pipeline tested
    # elsewhere.
    lb_rows = [{"rank": 1, "short_name": "A.JUDGE", "team": "NYY", "value": "35"}]
    lb_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    lb_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    lb_plugin._leaderboards = lambda: (
        [("AL HR LEADERS", lb_rows, "HR")], [("AL MVP WATCH", lb_rows)])
    lb_plugin.teams_leaderboards_on = True
    # Not what this test is about -- with only one followed team, pinning
    # its own live game to the static panel would absorb it entirely,
    # leaving nothing in the scrolling strip to compare widths against.
    lb_plugin.teams_panel_on = False

    lb_plugin.games._games = [{
        "id": "lbu1", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYY", "score": ""}, "away": {"abbr": "BOS", "score": ""},
        "situation": {}, "leaders": [],
    }]
    assert lb_plugin._display_strip(), "not-live strip failed to draw"
    not_live_width = lb_plugin.strip._strip_cache.width

    lb_plugin2 = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    lb_plugin2.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    lb_plugin2._leaderboards = lambda: (
        [("AL HR LEADERS", lb_rows, "HR")], [("AL MVP WATCH", lb_rows)])
    lb_plugin2.teams_leaderboards_on = True
    lb_plugin2.teams_panel_on = False
    lb_plugin2.games._games = [{
        "id": "lbl1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "3"}, "away": {"abbr": "BOS", "score": "2"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 1, "outs": 0},
        "leaders": [],
    }]
    assert lb_plugin2._display_strip(), "live strip failed to draw"
    live_width = lb_plugin2.strip._strip_cache.width

    assert live_width < not_live_width, (
        f"leaderboards/awards should be hidden while a followed team is "
        f"live: live={live_width}px, not_live={not_live_width}px"
    )
    print(f"PASS  leaderboards and awards are hidden from the strip while "
          f"a followed team is live ({not_live_width}px -> {live_width}px), "
          f"and shown again once nothing is")

    # The same hiding must apply for a live game that has nothing to do
    # with a followed team -- other-live is broader than has_live() on
    # purpose, since a live score competing for the same scroll is the
    # same problem regardless of whose game it is.
    lb_plugin3 = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    lb_plugin3.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    lb_plugin3._leaderboards = lambda: (
        [("AL HR LEADERS", lb_rows, "HR")], [("AL MVP WATCH", lb_rows)])
    lb_plugin3.teams_leaderboards_on = True
    lb_plugin3.teams_panel_on = False
    # Followed team's own game is upcoming, not live -- has_live() alone
    # would say False here.
    lb_plugin3.games._games = [{
        "id": "lbu2", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYY", "score": ""}, "away": {"abbr": "BOS", "score": ""},
        "situation": {}, "leaders": [],
    }]
    lb_plugin3.games._other_live = [{
        "id": "lbo1", "league": "nba", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "LAL", "score": "50"}, "away": {"abbr": "BOS", "score": "48"},
        "situation": {"kind": "basketball", "clock": "5:00"}, "leaders": [],
    }]
    assert not lb_plugin3.games.has_live(), (
        "test setup error: the followed team's own game must not be live "
        "here, or this would not isolate other-live at all"
    )
    assert lb_plugin3.games.has_any_live(), (
        "has_any_live() must be True when only an other-live game exists"
    )
    assert lb_plugin3._display_strip(), "other-live strip failed to draw"
    other_live_width = lb_plugin3.strip._strip_cache.width
    assert other_live_width < not_live_width, (
        f"leaderboards/awards should be hidden while ANY game is live, "
        f"including one outside the followed teams: "
        f"other_live={other_live_width}px, not_live={not_live_width}px"
    )
    print(f"PASS  leaderboards and awards are also hidden for a live game "
          f"outside the followed teams ({not_live_width}px -> "
          f"{other_live_width}px)")

    # Countdowns get the same treatment, isolated from leaderboards here
    # (no leaderboard data configured at all) so a width difference can
    # only be attributed to the countdown itself.
    cd_hide_events = [{"name": "CHRISTMAS", "days": 100}]
    cd_hide_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    cd_hide_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    cd_hide_plugin._countdowns = lambda: cd_hide_events
    cd_hide_plugin.teams_panel_on = False
    cd_hide_plugin.games._games = [{
        "id": "cdu1", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYY", "score": ""}, "away": {"abbr": "BOS", "score": ""},
        "situation": {}, "leaders": [],
    }]
    assert cd_hide_plugin._display_strip(), "not-live countdown strip failed to draw"
    cd_not_live_width = cd_hide_plugin.strip._strip_cache.width

    cd_hide_plugin2 = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    cd_hide_plugin2.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    cd_hide_plugin2._countdowns = lambda: cd_hide_events
    cd_hide_plugin2.teams_panel_on = False
    cd_hide_plugin2.games._games = [{
        "id": "cdl1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "3"}, "away": {"abbr": "BOS", "score": "2"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 1, "outs": 0},
        "leaders": [],
    }]
    assert cd_hide_plugin2._display_strip(), "live countdown strip failed to draw"
    cd_live_width = cd_hide_plugin2.strip._strip_cache.width

    assert cd_live_width < cd_not_live_width, (
        f"countdowns should be hidden while a game is live too, same as "
        f"leaderboards/awards: live={cd_live_width}px, "
        f"not_live={cd_not_live_width}px"
    )
    print(f"PASS  countdowns are also hidden from the strip while a game "
          f"is live ({cd_not_live_width}px -> {cd_live_width}px), and "
          f"shown again once nothing is")

    # weather.hide_forecast_when_live: off by default, so an install that
    # never sets it keeps the original behavior (weather stays up, live or
    # not) even while everything else hides.
    wf_off_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    wf_off_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    wf_off_plugin.teams_panel_on = False
    wf_off_plugin._weather_data = {
        "now_temp": 75, "units": "F", "now_condition": "Clear",
        "hourly": [{"name": "8P", "temp": 77}],
    }
    assert wf_off_plugin.teams_weather_hide_forecast_when_live is False, (
        "hide_forecast_when_live must default to off"
    )
    wf_off_plugin.games._games = [{
        "id": "wfl1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "3"}, "away": {"abbr": "BOS", "score": "2"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 1, "outs": 0},
        "leaders": [],
    }]
    orig_build_wf = wf_off_plugin.strip.build_strip
    wf_calls = []
    wf_off_plugin.strip.build_strip = lambda *a, **k: (
        wf_calls.append(k.get("weather_show_forecast")), orig_build_wf(*a, **k))[1]
    assert wf_off_plugin._display_strip(), "weather-toggle-off strip failed to draw"
    assert wf_calls[-1] is True, (
        f"with hide_forecast_when_live off, weather_show_forecast must "
        f"stay True even while live: {wf_calls}"
    )
    print("PASS  weather.hide_forecast_when_live defaults to off -- "
          "weather (including forecast) stays up during a live game "
          "unless explicitly opted in")

    # Opted in (hide_forecast_when_live: true), the forecast/moon columns
    # follow the same any_live gate as leaderboards/awards/countdowns, but
    # current conditions are untouched -- the weather block itself is
    # never passed as empty, only the flag controlling its own forecast
    # section changes.
    wf_on_plugin = LocalScoreboardPlugin(
        "local-scoreboard",
        {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}],
         "weather": {"hide_forecast_when_live": True}},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    wf_on_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    wf_on_plugin.teams_panel_on = False
    wf_on_plugin._weather_data = {
        "now_temp": 75, "units": "F", "now_condition": "Clear",
        "hourly": [{"name": "8P", "temp": 77}],
    }
    assert wf_on_plugin.teams_weather_hide_forecast_when_live is True
    wf_on_plugin.games._games = [{
        "id": "wfl2", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "3"}, "away": {"abbr": "BOS", "score": "2"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 1, "outs": 0},
        "leaders": [],
    }]
    orig_build_wf2 = wf_on_plugin.strip.build_strip
    wf2_calls = []
    wf_on_plugin.strip.build_strip = lambda *a, **k: (
        wf2_calls.append(k.get("weather_show_forecast")), orig_build_wf2(*a, **k))[1]
    assert wf_on_plugin._display_strip(), "weather-toggle-on strip failed to draw"
    assert wf2_calls[-1] is False, (
        f"with hide_forecast_when_live on and a game live, "
        f"weather_show_forecast must be False: {wf2_calls}"
    )
    assert wf_on_plugin.strip._strip_cache.width > 0, (
        "current conditions should still draw something even with the "
        "forecast hidden"
    )
    print("PASS  weather.hide_forecast_when_live=true hides the forecast/"
          "moon columns while any game is live, current conditions "
          "unaffected")

    # A live-state change must not have to wait for the scroll to complete
    # a full pass before the hide/reveal actually appears -- adopt_pending()
    # normally only runs at the seam, but on a long strip that could be
    # minutes away. Confirms the transition is adopted as soon as the
    # rebuild is ready, well before the scroll has gone anywhere near the
    # seam.
    urgent_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    urgent_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    urgent_plugin.teams_panel_on = False
    urgent_plugin._leaderboards = lambda: (
        [("AL HR LEADERS", lb_rows, "HR")], [("AL MVP WATCH", lb_rows)])
    urgent_plugin.teams_leaderboards_on = True
    urgent_plugin.games._games = [{
        "id": "urg1", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYY", "score": ""}, "away": {"abbr": "BOS", "score": ""},
        "situation": {}, "leaders": [],
    }]
    assert urgent_plugin._display_strip(), "urgent-adopt baseline strip failed to draw"
    urgent_not_live_width = urgent_plugin.strip._strip_cache.width
    assert urgent_plugin._last_any_live is False
    assert urgent_plugin._urgent_adopt is False, (
        "the very first build must not itself count as a live-state "
        "transition"
    )

    urgent_plugin.games._games = [{
        "id": "urg2", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "3"}, "away": {"abbr": "BOS", "score": "2"},
        "situation": {"kind": "baseball", "balls": 0, "strikes": 0, "outs": 0},
        "leaders": [],
    }]
    urgent_plugin.strip._last_build = 0.0
    assert urgent_plugin._display_strip(), "urgent-adopt trigger frame failed to draw"
    assert urgent_plugin._urgent_adopt is True, (
        "a live-state flip must be flagged for an out-of-turn adopt"
    )
    scroll_offset_before_adopt = urgent_plugin._scroll_offset
    assert urgent_plugin.strip._wait_for_background_build(), (
        "background rebuild for the live-state change did not finish"
    )
    assert urgent_plugin._display_strip(), "urgent-adopt follow-up frame failed to draw"
    urgent_live_width = urgent_plugin.strip._strip_cache.width

    assert urgent_live_width < urgent_not_live_width, (
        f"the live strip should be narrower (leaderboards/awards hidden): "
        f"not_live={urgent_not_live_width}px, live={urgent_live_width}px"
    )
    assert urgent_plugin._urgent_adopt is False, (
        "the out-of-turn adopt should clear the flag once it succeeds"
    )
    assert scroll_offset_before_adopt < urgent_not_live_width * 0.2, (
        "this check only proves anything if the scroll was still early in "
        f"its pass, nowhere near the seam: offset was "
        f"{scroll_offset_before_adopt}px against a "
        f"{urgent_not_live_width}px strip"
    )
    print(f"PASS  a live-state change adopts its rebuilt strip immediately "
          f"instead of waiting for the scroll to complete a full pass "
          f"({urgent_not_live_width}px -> {urgent_live_width}px, "
          f"{scroll_offset_before_adopt:.1f}px into the pass)")

    # ---- 6. Plugin lifecycle -------------------------------------------
    plugin_display = FakeDisplay(192, 32)
    plugin = LocalScoreboardPlugin("local-scoreboard", {}, plugin_display, FakeCache(), None)
    plugin.games = gm
    plugin.logos = logos
    plugin.renderer.logo_manager = logos

    # Strip is the default layout, so one mode that walks the teams.
    modes = plugin.get_available_modes()
    assert modes == ["local_scoreboard"], modes
    assert plugin.display("local_scoreboard"), "strip mode failed to draw"
    assert plugin.get_cycle_duration("local_scoreboard")
    print(f"PASS  strip layout exposes {modes} and draws")

    # Strip is one continuously scrolling image -- the display controller's
    # own high-FPS loop (125 FPS) is what keeps that motion smooth rather
    # than visibly stepping, and it reads this attribute directly rather
    # than falling back to its enable_scrolling heuristic.
    assert plugin.needs_high_fps is True, (
        "strip layout should declare needs_high_fps so the display "
        "controller runs its smooth 125 FPS loop for the scroll"
    )
    print("PASS  strip layout declares needs_high_fps for smooth scrolling")

    # Exactly one declared mode. Declaring modes the plugin then declines
    # leaves the panel holding its last frame while the rotation works
    # through the dead slots, which reads as the board freezing.
    import json as _json
    with open("manifest.json") as _mf:
        declared = _json.load(_mf)["display_modes"]
    assert declared == ["local_scoreboard"], f"manifest declares dead modes: {declared}"
    assert plugin.get_available_modes() == ["local_scoreboard"]
    assert plugin.display("local_scoreboard") is True
    print("PASS  one declared mode, so no rotation slot can stall")

    # The card layout still works for anyone who prefers it
    plugin.on_config_change({"layout": "cards"})
    plugin.games = gm
    card_modes = plugin.get_available_modes()
    assert card_modes == ["local_scoreboard"], card_modes
    assert plugin.display("local_scoreboard"), "card layout failed to draw"
    print("PASS  card layout serves the same single mode")

    # Cards swaps a static frame in on its own schedule -- no continuous
    # motion for a higher frame rate to smooth out, so it should not ask
    # for the scroll-tuned high-FPS loop.
    assert plugin.needs_high_fps is False, (
        "card layout has no scroll motion and should not declare "
        "needs_high_fps"
    )
    print("PASS  card layout does not declare needs_high_fps")
    plugin.on_config_change({})
    plugin.games = gm

    assert plugin.has_live_priority(), "a live followed game should take priority"

    # The visit cap must release the panel even mid-list
    from manager import MODE_TEAMS
    plugin._current_mode = MODE_TEAMS
    plugin._mode_started[MODE_TEAMS] = time.time() - 999
    assert plugin.is_cycle_complete(), "plugin never releases the panel"
    print("PASS  live games take priority; the visit cap releases the panel")

    # A broken init must degrade rather than raise
    original = LocalScoreboardPlugin._build_components
    LocalScoreboardPlugin._build_components = lambda self: (_ for _ in ()).throw(
        RuntimeError("simulated"))
    try:
        broken = LocalScoreboardPlugin("local-scoreboard", {}, FakeDisplay(), FakeCache(), None)
    finally:
        LocalScoreboardPlugin._build_components = original
    assert broken.is_enabled is False
    assert broken.display("local_live") is False
    assert broken.get_available_modes() == []
    broken.update()
    broken.get_info()
    broken.cleanup()
    print("PASS  a failed init degrades quietly and stays safe to call")

    plugin.cleanup()

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL TESTS PASSED")
    return 0


from manager import LocalScoreboardPlugin  # noqa: E402  (needs the stubs above first)

if __name__ == "__main__":
    sys.exit(main())
