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
import tempfile
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
# Canned ESPN Payloads
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

SCOREBOARD_SOCCER = {"events": [
    event("701", ("GET", "Getafe", "0-0-0"), ("BAR", "Barcelona", "0-0-0"),
          "0", "2", "in", False, "67'", clock="67'", period=2),
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

    from espn_data_source import LEAGUES
    assert LEAGUES.get("laliga") == ("soccer", "esp.1"), (
        f"laliga must map to ESPN's soccer/esp.1 path: {LEAGUES.get('laliga')}"
    )
    soccer = src._parse_events(SCOREBOARD_SOCCER, "laliga")
    assert soccer[0]["state"] == STATE_LIVE
    assert soccer[0]["home"]["abbr"] == "BAR"
    assert soccer[0]["clock"] == "67'"
    # Soccer has no ESPN "situation" object; we still surface the minute as
    # a soccer live-detail so the strip can draw it beside the crests.
    assert soccer[0]["situation"].get("kind") == "soccer", soccer[0]["situation"]
    assert soccer[0]["situation"].get("clock") == "67'", soccer[0]["situation"]
    print("PASS  the same parser handles a soccer (La Liga) payload too, "
          "with a soccer live-detail minute")

    # ---- 2. Names ------------------------------------------------------
    assert abbreviate_name("Aaron Judge") == "A.Judge"
    assert abbreviate_name("Ronald Acuna Jr.") == "R.Acuna"
    assert abbreviate_name("Jos\u00e9 Ram\u00edrez") == "J.Ramirez"
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
    assert board_leaders and board_leaders[0]["name"] == "A.Judge", board_leaders
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
        {"team": "NYY", "name": "G.Cole", "line": "7.0 IP, 2 ER", "side": "pitching"},
        {"team": "NYY", "name": "A.Judge", "line": "2-4, HR", "side": "batting"},
        {"team": "BOS", "name": "J.Duran", "line": "3-4, 2B", "side": "batting"},
    ]
    won = {"state": STATE_FINAL, "league": "mlb", "leaders": perf_leaders,
           "home": {"abbr": "NYY", "winner": True},
           "away": {"abbr": "BOS", "winner": False}}
    lost = {"state": STATE_FINAL, "league": "mlb", "leaders": perf_leaders,
            "home": {"abbr": "NYY", "winner": False},
            "away": {"abbr": "BOS", "winner": True}}
    on_win = ESPNGamesSource.pick_performer(won, "NYY")
    on_loss = ESPNGamesSource.pick_performer(lost, "NYY")
    assert on_win["name"] == "A.Judge", on_win
    assert on_win["side"] == "batting", "a pitching line was chosen"
    assert on_loss["name"] == "J.Duran", on_loss
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
    assert by_team["NYY"]["name"] == "A.Judge", by_team["NYY"]
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
    assert leaders[0]["name"] == "A.Judge"
    assert "HR" in leaders[0]["line"]
    assert src._parse_leaders({}, 2) == []
    print(f"PASS  notable players parsed: {[(l['name'], l['line']) for l in leaders]}")

    # A fixture must always say which day. A bare time is ambiguous on a
    # board you glance at -- 7:05 tonight and 7:05 next Tuesday look the same.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _now = _dt.now().astimezone()
    for label, when, expect_day in [
        ("today", _now.replace(hour=19, minute=5), "Today"),
        ("in 3 days", _now + _td(days=3), None),
        ("in 10 days", _now + _td(days=10), None),
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
        if expect_day:
            assert parts[0] == expect_day, f"{label}: {got!r}"
        else:
            # Same Title Case as the forecast columns ("Mon", not "MON").
            assert parts[0] == parts[0].title() and parts[0] != parts[0].upper(), (
                f"{label}: day abbr should be Title Case like forecast: {got!r}"
            )
            assert parts[0] == ESPNGamesSource.day_abbr(when.astimezone()), (
                f"{label}: {got!r}"
            )
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

    # Market-only entries (no names[]) used to paint "home"/"away" as if
    # they were networks -- skip those, and reject those strings even if
    # they somehow appear in names[].
    market_only = {"broadcasts": [
        {"market": "home"},
        {"market": "national", "names": ["ESPN"]},
    ]}
    assert src._parse_broadcast(market_only) == "ESPN", (
        f"market-only entries must not become channel names: "
        f"{src._parse_broadcast(market_only)!r}"
    )
    garbage_names = {"broadcasts": [
        {"market": "home", "names": ["home"]},
        {"market": "national", "names": ["FOX"]},
    ]}
    assert src._parse_broadcast(garbage_names) == "FOX", (
        f"literal 'home'/'away' names must be filtered: "
        f"{src._parse_broadcast(garbage_names)!r}"
    )
    print("PASS  broadcast parsing ignores market-only / home-away garbage")

    # Football possession: ESPN sometimes points situation.possession at
    # team.id rather than competitor.id -- match either.
    nfl_sit_team_id = ESPNGamesSource._parse_situation({
        "situation": {
            "possession": "6",
            "shortDownDistanceText": "1st & 10",
            "possessionText": "DAL 40",
            "isRedZone": False,
        },
        "status": {"displayClock": "5:00"},
        "competitors": [
            {"id": "1", "team": {"id": "6", "abbreviation": "DAL"}},
            {"id": "2", "team": {"id": "26", "abbreviation": "SEA"}},
        ],
    }, "nfl")
    assert nfl_sit_team_id.get("possession") == "DAL", (
        f"possession must resolve via team.id when competitor.id differs: "
        f"{nfl_sit_team_id}"
    )
    mlb_sit_bad = ESPNGamesSource._parse_situation({
        "situation": {"balls": "x", "strikes": None, "outs": "2",
                      "onFirst": True},
    }, "mlb")
    assert mlb_sit_bad["balls"] == 0 and mlb_sit_bad["strikes"] == 0
    assert mlb_sit_bad["outs"] == 2 and mlb_sit_bad["first"] is True
    print("PASS  NFL possession matches team.id; baseball counts tolerate junk")

    import inspect as _inspect_espn
    _sb_sig = _inspect_espn.signature(ESPNGamesSource.fetch_scoreboard)
    assert _sb_sig.parameters["days_back"].default == 3, (
        f"fetch_scoreboard days_back default should be 3 (history window), "
        f"got {_sb_sig.parameters['days_back'].default}"
    )
    print("PASS  scoreboard history window defaults to 3 days")

    # ---- 4. Team Filtering And Cadence ----------------------------------
    gm = GamesManager(log, cache_manager=FakeCache())

    class StubSource:
        def fetch_scoreboard(self, league, **kwargs):
            return {"mlb": mlb, "nba": nba, "nfl": nfl}.get(league, [])

        def fetch_leaders(self, league, event_id, per_game=2):
            return src._parse_leaders(SUMMARY, per_game, league)

        def fetch_batting(self, league, event_id):
            # Baseball reads the boxscore, since its summary has no leaders.
            return [{"team": "NYY", "name": "A.Judge", "line": "2-4, HR, 3 RBI",
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
    logo_dir = os.path.join(tempfile.gettempdir(), "_nyc_logos")
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

    # Soccer's crest CDN wants ESPN's own numeric team id, not the
    # abbreviation every other league here uses -- confirmed against a
    # real request, where the abbreviation path 404s and the numeric one
    # doesn't. Spied without a real network call.
    import logo_manager as _logo_mod

    class _StubResponse:
        status_code = 404
        content = b""

    url_calls = []

    def _stub_get(url, **kw):
        url_calls.append(url)
        return _StubResponse()

    logos_soccer = TeamLogoManager(log, cache_dir=logo_dir, allow_download=True)
    orig_requests_get = _logo_mod.requests.get
    _logo_mod.requests.get = _stub_get
    try:
        logos_soccer._download("soccer", "BAR")
    finally:
        _logo_mod.requests.get = orig_requests_get
    assert any(u.endswith("/83.png") for u in url_calls), (
        f"Barcelona's logo request should use ESPN's numeric team id "
        f"(83), not the abbreviation: {url_calls}"
    )
    assert not any(u.lower().endswith("/bar.png") for u in url_calls), (
        f"must not also try the plain abbreviation path, confirmed 404 "
        f"for this league: {url_calls}"
    )
    print(f"PASS  soccer logo downloads use ESPN's numeric team id "
          f"override instead of the abbreviation: {url_calls}")

    # Plugin league key is "laliga"; ESPN's crest CDN folder is "soccer".
    # Downloads that keep the plugin key 404 and then stick in _misses for
    # the whole session -- Barcelona (and any La Liga crest) stays blank.
    url_calls.clear()
    logos_laliga = TeamLogoManager(log, cache_dir=logo_dir, allow_download=True)
    _logo_mod.requests.get = _stub_get
    try:
        logos_laliga._download("laliga", "BAR")
    finally:
        _logo_mod.requests.get = orig_requests_get
    assert any("/soccer/500/83.png" in u for u in url_calls), (
        f"laliga crest downloads must hit ESPN's soccer CDN folder, not "
        f"laliga: {url_calls}"
    )
    assert not any("/laliga/500/" in u for u in url_calls), (
        f"must not request the plugin league key as a CDN folder: {url_calls}"
    )
    print("PASS  laliga crest downloads remap to ESPN's soccer CDN folder")

    # Opponent crests: Real Madrid (and the rest of La Liga) must resolve
    # via the numeric-id table, not blank out as text abbreviations.
    assert "RMA" in _logo_mod.ESPN_LOGO_ID_OVERRIDES
    assert "GET" in _logo_mod.ESPN_LOGO_ID_OVERRIDES
    logos_rma = TeamLogoManager(log, cache_dir=logo_dir, allow_download=True)
    url_calls.clear()
    _logo_mod.requests.get = _stub_get
    try:
        logos_rma._download("laliga", "RMA")
        logos_rma._download("laliga", "GET", espn_id="2922")
    finally:
        _logo_mod.requests.get = orig_requests_get
    assert any("/soccer/500/86.png" in u for u in url_calls), url_calls
    assert any("/soccer/500/2922.png" in u for u in url_calls), url_calls
    print("PASS  La Liga opponent crests download via ESPN team ids")

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
        os.path.join(tempfile.gettempdir(), "nyc_cards.png"))

    # ---- 5b. Team Strips -----------------------------------------------
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
                "leaders": [{"category": "TOP", "name": "A.Judge",
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
        "leaders": [{"category": "HR", "name": "A.Judge", "line": "2-4, HR"}],
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
    rnote2._draw_note(long_note_img, long_note_spy, 2, "Aaron Judge", "A.Judge",
                      "2-4, HR, 3 RBI, 2 R, BB", note_font, note_row_h)
    assert "Aaron Judge" in long_note_spy.calls, (
        f"a stat line wider than the short name should show the full name: "
        f"{long_note_spy.calls}"
    )

    short_note_img = Image.new("RGB", (150, 32), (0, 0, 0))
    short_note_spy = _NoteSpyDraw(_NoteID.Draw(short_note_img))
    rnote2._draw_note(short_note_img, short_note_spy, 2, "Aaron Judge", "A.Judge",
                      "HR", note_font, note_row_h)
    assert "A.Judge" in short_note_spy.calls and "Aaron Judge" not in short_note_spy.calls, (
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
        ("MLB HR Leaders", [
            {"rank": 1, "short_name": "C.Raleigh", "team": "SEA", "value": "48"},
            {"rank": 2, "short_name": "A.Judge", "team": "NYY", "value": "41"}]),
        ("MLB ERA Leaders", [
            {"rank": 1, "short_name": "T.Skubal", "team": "DET", "value": "2.49"}]),
    ])
    assert with_boards.width > without_boards.width, (
        "leaderboards did not extend the strip"
    )
    # An empty leaderboard must add nothing rather than a blank segment
    rboard._strip_key = None
    rempty = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    with_empty = rempty.build_strip([board_team],
                                    leaderboards=[("MLB HR Leaders", [])])
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

    # A divider must separate the section banner itself ("MLB Season
    # Leaders") from the first category's content -- without it, the title
    # ran directly into "AL HR Leaders" with only a small gap and no line.
    dsep = FakeDisplay(192, 32)
    rsep = StripRenderer(dsep, {}, log, logo_manager=logos)
    sep_rows = [{"rank": 1, "short_name": "C.Raleigh", "team": "SEA",
                "value": "48"}]
    sep_strip = rsep.build_strip(
        [], leaderboards=[("AL HR Leaders", sep_rows, "HR")],
        awards=[("AL MVP Watch", sep_rows)],
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
        [excess_team], leaderboards=[("AL HR Leaders", excess_rows, "HR")],
        awards=[("AL MVP Watch", excess_rows)], other_live=excess_other_live,
        team_mvps={"NYY": {"name": "Aaron Judge", "short_name": "A.Judge",
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

    # The "LIVE / AROUND THE LEAGUE" banner must be followed by a divider
    # before its own first game, the same as every other section banner
    # (leaderboards, awards) already is -- missing here previously, which
    # read as the banner and the game running together as one block
    # instead of a header over its own content. Checked by call order,
    # not pixels: _draw_divider must run between _draw_live_section and
    # the _draw_game that follows it.
    rlive_div = StripRenderer(FakeDisplay(192, 32), {}, log, logo_manager=logos)
    live_div_calls = []
    orig_live_section = rlive_div._draw_live_section
    orig_divider = rlive_div._draw_divider
    orig_game = rlive_div._draw_game

    def _spy_live_section(*a, **kw):
        live_div_calls.append("live_section")
        return orig_live_section(*a, **kw)

    def _spy_divider(*a, **kw):
        live_div_calls.append("divider")
        return orig_divider(*a, **kw)

    def _spy_game(*a, **kw):
        live_div_calls.append("game")
        return orig_game(*a, **kw)

    rlive_div._draw_live_section = _spy_live_section
    rlive_div._draw_divider = _spy_divider
    rlive_div._draw_game = _spy_game

    single_other_live = [{
        "id": "divo1", "league": "nba", "state": STATE_LIVE, "start": "",
        "away": {"abbr": "BOS", "score": "50"}, "home": {"abbr": "LAL", "score": "48"},
        "situation": {"kind": "basketball", "clock": "5:00"}, "leaders": [],
    }]
    rlive_div.build_strip(
        [({"abbr": "NYY", "league": "mlb", "name": "Yankees"},
          [dict(both_game, id="divf1")])],
        other_live=single_other_live,
    )
    live_idx = live_div_calls.index("live_section")
    assert live_div_calls[live_idx + 1] == "divider", (
        f"expected a divider immediately after the live-around-the-league "
        f"banner, before its first game: {live_div_calls}"
    )
    print("PASS  a divider separates the 'Live Around The League' banner "
          "from its own first game")

    # A leaderboard segment is a table: names start at one column and values
    # end at another, measured across every row, so figures align vertically.
    align_rows = [
        {"rank": 1, "short_name": "A.Judge", "team": "NYY", "value": ".331"},
        {"rank": 2, "short_name": "J.Soto", "team": "NYM", "value": ".312"},
        {"rank": 3, "short_name": "B.Witt", "team": "KC", "value": ".305"},
    ]
    dalign = FakeDisplay(192, 32)
    ralign = StripRenderer(dalign, {}, log)
    seg = ralign.build_strip([], leaderboards=[("AL AVG Leaders", align_rows, "AVG")])
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

    # use_process=True: composing runs in a genuinely separate OS process
    # instead of a background thread sharing this one's GIL -- the fix
    # for a periodic scroll pause that throttling and caching alone
    # couldn't fully remove, since the underlying compose cost never
    # went away, only how often it was paid. Off by default (every
    # StripRenderer above this point used the thread path unchanged);
    # only manager.py's real, on-device instance opts in. First build
    # stays synchronous either way -- nothing to keep showing yet.
    dproc = FakeDisplay(192, 32)
    rproc = StripRenderer(dproc, {}, log, use_process=True)
    assert rproc._compose_process is None, (
        "the worker process must not start until the first background "
        "dispatch actually needs one"
    )
    proc_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    proc_game = {"id": "pw1", "league": "mlb", "state": STATE_LIVE, "start": "",
                "away": {"abbr": "BOS", "score": "2"},
                "home": {"abbr": "NYY", "score": "3"},
                "situation": {"kind": "baseball", "balls": 0, "strikes": 0,
                              "outs": 0}, "leaders": []}
    first = rproc.build_strip([(proc_team, [proc_game])])
    assert first is not None, "first (synchronous) build failed"
    assert rproc._compose_process is None, (
        "the synchronous first build must not need the worker process either"
    )

    rproc._last_build = 0.0
    moved_proc = dict(proc_game, situation=dict(proc_game["situation"], balls=2))
    still_showing = rproc.build_strip([(proc_team, [moved_proc])])
    assert still_showing is not None, (
        "dispatching a process-backed background build must not block "
        "the caller"
    )
    assert rproc._compose_process is not None and rproc._compose_process.is_alive(), (
        "the worker process should be running once a background build "
        "has actually been dispatched"
    )
    assert rproc._wait_for_background_build(timeout=10), (
        "process-backed background build did not finish"
    )
    assert rproc.has_pending(), "worker process did not deliver a pending strip"
    assert rproc.adopt_pending(), "failed to adopt the worker's own strip"
    print("PASS  use_process=True composes in a separate OS process, "
          "started lazily on the first background dispatch")

    # A worker-side failure must be caught the same way a thread-side one
    # is -- an unpicklable value in the request is a reliable way to
    # force a real failure at the process boundary itself (pickling
    # happens synchronously in put(), before anything reaches the
    # worker), without needing to reach into a separate process's own
    # internals to break it.
    dproc2 = FakeDisplay(192, 32)
    rproc2 = StripRenderer(dproc2, {}, log, use_process=True)
    rproc2.build_strip([(proc_team, [proc_game])])  # first build, synchronous
    rproc2._last_build = 0.0
    unpicklable_weather = {"now_temp": 75, "units": "F",
                           "callback": lambda: None}
    moved_proc2 = dict(proc_game, situation=dict(proc_game["situation"], balls=3))
    still_showing2 = rproc2.build_strip(
        [(proc_team, [moved_proc2])], weather=unpicklable_weather)
    assert still_showing2 is not None, (
        "a worker-process dispatch failure should not crash the caller"
    )
    assert rproc2._wait_for_background_build(timeout=10), (
        "background build did not finish"
    )
    assert not rproc2.has_pending(), (
        "a failed worker dispatch should not leave a bogus pending strip"
    )
    assert rproc2._dispatched_signature is None, (
        "a failed worker dispatch must clear the in-flight marker"
    )
    print("PASS  a worker-process compose failure is also caught cleanly, "
          "same as a thread-side one")

    # close() stops the worker cleanly on a plugin disable/reload, rather
    # than relying purely on daemon=True to clean it up whenever the
    # whole service eventually restarts. Safe to call whether or not a
    # worker was ever actually started.
    dproc3 = FakeDisplay(192, 32)
    rproc3 = StripRenderer(dproc3, {}, log, use_process=True)
    rproc3.close()  # no worker ever started -- must not raise
    rproc3.build_strip([(proc_team, [proc_game])])
    rproc3._last_build = 0.0
    moved_proc3 = dict(proc_game, situation=dict(proc_game["situation"], balls=1))
    rproc3.build_strip([(proc_team, [moved_proc3])])
    assert rproc3._wait_for_background_build(timeout=10)
    worker = rproc3._compose_process
    assert worker is not None and worker.is_alive(), (
        "test setup error: expected a live worker process to close"
    )
    rproc3.close()
    worker.join(3.0)
    assert not worker.is_alive(), "close() should stop the worker process"
    print("PASS  close() stops the worker process cleanly")

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
        [], leaderboards=[("AL AVG Leaders", align_rows, "AVG")])
    assert header_seg.width >= seg.width
    print("PASS  title and column header sit side by side")

    # Rank 1/2/3 must be gold/silver/bronze, and a row's team abbreviation
    # must be that team's own colour, not the rank colour -- drawn as two
    # separate runs of text, so a name and its team read as visually
    # distinct pieces rather than one flat-coloured line.
    medal_rows = [
        {"rank": 1, "short_name": "A.Judge", "team": "NYY", "value": "58"},
        {"rank": 2, "short_name": "R.Devers", "team": "BOS", "value": "42"},
        {"rank": 3, "short_name": "V.Guerrero", "team": "TOR", "value": "40"},
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
    rmedal._draw_leaderboard(dmedal, medal_draw, 2, "HR Leaders", medal_rows,
                             medal_font, medal_row_h, "HR")
    medal_draw.text = real_text

    by_text = {text: fill for text, fill in calls}
    assert by_text.get("1.A.Judge") == StripRenderer.GOLD, (
        f"rank 1 name drawn in {by_text.get('1.A.Judge')}, not gold"
    )
    assert by_text.get("2.R.Devers") == StripRenderer.SILVER, (
        f"rank 2 name drawn in {by_text.get('2.R.Devers')}, not silver"
    )
    assert by_text.get("3.V.Guerrero") == StripRenderer.BRONZE, (
        f"rank 3 name drawn in {by_text.get('3.V.Guerrero')}, not bronze"
    )
    assert by_text.get("NYY") == StripRenderer.TEAM_COLORS["NYY"], (
        f"NYY drawn in {by_text.get('NYY')}, not its own team colour"
    )
    assert by_text.get("BOS") == StripRenderer.TEAM_COLORS["BOS"], (
        f"BOS drawn in {by_text.get('BOS')}, not its own team colour"
    )
    print("PASS  ranks 1-3 render gold/silver/bronze, team abbreviations in "
          "their own team's colour")

    # All 3 ranked rows must render specifically at real hardware's own
    # row_h=8, not just whatever row_h this sandbox's fallback font
    # happens to measure -- title-plus-3-rows silently dropped the 3rd
    # row at exactly this row_h once, since the shared body font is sized
    # to fill 4 rows across the *whole* panel height with nothing held
    # back for the 1px margin every other segment reserves, and 4*8=32
    # left no room for it. Confirmed against a real render on the Pi, not
    # just this forced-row_h simulation.
    import strip_renderer as _lb8mod
    _lb8_orig_fit = _lb8mod.StripRenderer._fit_font

    def _lb8_forced(self, draw, rows, avail, min_row_h=None):
        f, _ = _lb8_orig_fit(self, draw, rows, avail)
        return f, 8

    _lb8mod.StripRenderer._fit_font = _lb8_forced
    try:
        rlb8 = StripRenderer(FakeDisplay(192, 32), {}, log)
        lb8_calls = []
        lb8_img = Image.new("RGB", (220, 32), (0, 0, 0))
        from PIL import ImageDraw as _LB8ID
        lb8_draw = _LB8ID.Draw(lb8_img)
        lb8_real_text = lb8_draw.text

        def _lb8_spy(xy, text, font=None, fill=None, **kw):
            lb8_calls.append(text)
            return lb8_real_text(xy, text, font=font, fill=fill, **kw)

        lb8_draw.text = _lb8_spy
        lb8_font, lb8_row_h = rlb8._fit_font(lb8_draw, 4, rlb8.height)
        assert lb8_row_h == 8, f"test setup error: expected row_h=8, got {lb8_row_h}"
        rlb8._draw_leaderboard(lb8_img, lb8_draw, 2, "HR Leaders", medal_rows,
                               lb8_font, lb8_row_h, "HR")
    finally:
        _lb8mod.StripRenderer._fit_font = _lb8_orig_fit
    assert "1.A.Judge" in lb8_calls, f"rank 1 missing at row_h=8: {lb8_calls}"
    assert "2.R.Devers" in lb8_calls, f"rank 2 missing at row_h=8: {lb8_calls}"
    assert "3.V.Guerrero" in lb8_calls, (
        f"rank 3 was silently dropped at row_h=8, the real hardware row "
        f"height, even though it draws fine at this sandbox's own "
        f"natural row_h: {lb8_calls}"
    )
    print("PASS  all 3 ranked rows render at real hardware's row_h=8, not "
          "just this sandbox's own natural row height")

    # The football possession marker used to be a Unicode "●", which real
    # BDF fonts can't encode at all -- UnicodeEncodeError on the Pi the
    # moment a followed team's opponent had the ball. Fixed by switching to
    # plain "*". Two separate draw sites had this bug (the scrolling strip's
    # live detail, and the static panel's possession ticker); both need to
    # actually draw "*" now, not just avoid crashing.
    poss_img = Image.new("RGB", (220, 32), (0, 0, 0))
    from PIL import ImageDraw as _PossID
    poss_draw = _PossID.Draw(poss_img)
    poss_real_text = poss_draw.text
    poss_calls = []

    def _poss_spy(xy, text, font=None, fill=None, **kw):
        poss_calls.append(text)
        return poss_real_text(xy, text, font=font, fill=fill, **kw)

    poss_draw.text = _poss_spy
    poss_renderer = StripRenderer(FakeDisplay(192, 32), {}, log)
    poss_font, poss_row_h = poss_renderer._fit_font(poss_draw, 2, poss_renderer.height)
    poss_game = {
        "id": "p1", "league": "nfl", "state": STATE_LIVE, "start": "",
        "away": {"abbr": "BUF", "score": "14"},
        "home": {"abbr": "NYG", "score": "10"},
        "situation": {"kind": "football", "down_distance": "2nd & 5",
                     "yard_line": "NYG 30", "possession": "NYG",
                     "red_zone": False},
    }
    poss_renderer._draw_live_detail(poss_img, poss_draw, 0, poss_game,
                                    poss_font, poss_row_h)
    assert any("*" in t for t in poss_calls), (
        f"possession marker missing entirely from the strip's live detail: "
        f"{poss_calls}"
    )
    assert not any("●" in t for t in poss_calls), (
        f"raw Unicode possession marker reached draw.text, will crash on "
        f"real BDF fonts: {poss_calls}"
    )
    print("PASS  strip live-detail draws the football possession marker as "
          "ASCII \"*\", not the Unicode \"●\" that crashes real BDF fonts")

    # Football live detail must reserve width for the yard-line row, not
    # just possession + down. Drawing "SEA 35" (or any spot) without
    # advancing the returned width is what made the next strip segment
    # paint through the live card during other-live NFL games.
    from PIL import ImageDraw as _SpotID
    spot_img = Image.new("RGB", (220, 32), (0, 0, 0))
    spot_draw = _SpotID.Draw(spot_img)
    spot_renderer = StripRenderer(FakeDisplay(192, 32), {}, log)
    spot_font, spot_row_h = spot_renderer._fit_font(
        spot_draw, 2, spot_renderer.height)
    spot_game = {
        "id": "spot1", "league": "nfl", "state": STATE_LIVE, "start": "",
        "away": {"abbr": "DAL", "score": "14"},
        "home": {"abbr": "SEA", "score": "7"},
        "situation": {"kind": "football", "down_distance": "1st & 10",
                      "yard_line": "SEA 35", "possession": "DAL",
                      "red_zone": False},
    }
    spot_w = spot_renderer._draw_live_detail(
        spot_img, spot_draw, 0, spot_game, spot_font, spot_row_h)
    spot_ink_right = 0
    spot_px = spot_img.load()
    for _x in range(spot_img.width):
        for _y in range(spot_img.height):
            if sum(spot_px[_x, _y]) > 30:
                spot_ink_right = _x
    assert spot_w >= spot_ink_right + 1, (
        f"football live-detail returned width {spot_w} but ink reaches "
        f"x={spot_ink_right} -- next strip segment will overlap the "
        f"yard line (DAL@SEA other-live regression)"
    )
    print("PASS  football live-detail width covers the yard-line row, "
          "not only possession + down")

    # Same check for the static panel's own possession ticker -- a separate
    # code path (render_static_panel), separate draw call, same bug class.
    import PIL.ImageDraw as _PanelIDMod
    _panel_orig_text = _PanelIDMod.ImageDraw.text
    panel_poss_calls = []

    def _panel_poss_spy(self, xy, text, font=None, fill=None, **kw):
        panel_poss_calls.append(text)
        return _panel_orig_text(self, xy, text, font=font, fill=fill, **kw)

    _PanelIDMod.ImageDraw.text = _panel_poss_spy
    try:
        poss_panel_renderer = StripRenderer(FakeDisplay(64, 32), {}, log,
                                            logo_manager=logos)
        poss_panel = poss_panel_renderer.render_static_panel(poss_game, "NYG", 64)
    finally:
        _PanelIDMod.ImageDraw.text = _panel_orig_text
    assert poss_panel is not None
    assert any(t.endswith("*") for t in panel_poss_calls), (
        f"possession marker missing entirely from the static panel's "
        f"ticker: {panel_poss_calls}"
    )
    assert not any("●" in t for t in panel_poss_calls), (
        f"raw Unicode possession marker reached draw.text on the static "
        f"panel, will crash on real BDF fonts: {panel_poss_calls}"
    )
    print("PASS  static panel draws the football possession marker as "
          "ASCII \"*\" too, not the Unicode \"●\" that crashes real "
          "BDF fonts")

    # AL and NL get their own mark instead of a text prefix; MLB (the merged
    # scope) keeps the text label since it has no mark of its own here.
    from awards_manager import AWARD_DEFINITIONS as _AD
    assert "MVP" == _AD["mvp"]["label"], (
        "award label still carries the redundant 'WATCH' the section "
        "banner already says once"
    )
    print("PASS  award labels no longer repeat 'WATCH' the section banner "
          "already carries")

    # ---- 5c. Logo Manager: AL/NL Marks -----------------------------------
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
            return [{"rank": 1, "name": "Test Player", "short_name": "T.Player",
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
    assert "AL HR Leaders" in titles and "NL HR Leaders" in titles, (
        f"AL/NL board lost its text prefix: {titles}"
    )
    assert "MLB HR Leaders" in titles, (
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
            return [{"rank": 1, "name": "Aaron Judge", "short_name": "A.Judge",
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

    # show_clock=False drops the clock from the scroll entirely -- the
    # scroll's own copy only earns its place when the static panel isn't
    # already showing the time (a live game having bumped the clock out of
    # that slot); otherwise it's the same clock shown twice. Measured on
    # real leaderboard content, since an empty strip floors to a fixed
    # minimum width regardless of the clock, which would mask the
    # difference.
    clock_rows = [{"rank": 1, "short_name": "A.Judge", "team": "NYY", "value": "35"}]
    dnoclock = FakeDisplay(192, 32)
    rnoclock = StripRenderer(dnoclock, {}, log)
    shown = rnoclock.build_strip(
        [], leaderboards=[("AL HR Leaders", clock_rows, "HR")],
        clock=_clock_dt(2026, 8, 9, 19, 5), show_clock=True)
    assert rnoclock._clock_box, "clock position should be recorded when shown"

    rnoclock2 = StripRenderer(FakeDisplay(192, 32), {}, log)
    hidden = rnoclock2.build_strip(
        [], leaderboards=[("AL HR Leaders", clock_rows, "HR")],
        clock=_clock_dt(2026, 8, 9, 19, 5), show_clock=False)
    assert not rnoclock2._clock_box, (
        "no clock position should be recorded when show_clock is False"
    )
    assert hidden.width < shown.width, (
        f"show_clock=False should drop the clock from the scroll: "
        f"shown={shown.width}px, hidden={hidden.width}px"
    )
    print(f"PASS  show_clock=False drops the clock from the scroll "
          f"({shown.width}px -> {hidden.width}px)")

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
                      "batter": "J.Soto", "pitcher": "G.Cole"},
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

    # The scroll's own clock is redundant whenever the static panel is
    # already showing the time (the clock/weather fallback, nothing live)
    # -- it only earns its place once a live game has bumped the clock out
    # of that slot. A second followed team with its own upcoming game keeps
    # the scroll non-empty even once the panel absorbs the first team's
    # live game entirely -- with only one followed team, that game would
    # be pinned to the panel *and* excluded from the scroll, leaving
    # nothing there to check _clock_box against at all.
    sc_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [
            {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
            {"abbr": "NYM", "league": "mlb", "name": "Mets"},
        ]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    sc_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
        {"abbr": "NYM", "league": "mlb", "name": "Mets"},
    ])
    sc_plugin.teams_panel_priority = ["NYY"]
    orig_sc_build = sc_plugin.strip.build_strip
    sc_calls = []
    sc_plugin.strip.build_strip = lambda *a, **k: (
        sc_calls.append(k.get("weather_show_current")), orig_sc_build(*a, **k))[1]
    sc_plugin.games._games = [{
        "id": "scu1", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYY", "score": ""}, "away": {"abbr": "BOS", "score": ""},
        "situation": {}, "leaders": [],
    }, {
        "id": "scu2", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYM", "score": ""}, "away": {"abbr": "ATL", "score": ""},
        "situation": {}, "leaders": [],
    }]
    assert sc_plugin._display_strip(), "not-live strip failed to draw"
    assert not sc_plugin.strip._clock_box, (
        "the scroll should not carry its own clock while the static panel "
        "is already showing the clock/weather fallback"
    )
    assert sc_calls[-1] is False, (
        f"weather_show_current must be False alongside show_clock=False -- "
        f"the panel is already showing current conditions too: {sc_calls}"
    )

    sc_plugin.games._games = [{
        "id": "scl1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "3"}, "away": {"abbr": "BOS", "score": "2"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 1, "outs": 0},
        "leaders": [],
    }, {
        "id": "scu2", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYM", "score": ""}, "away": {"abbr": "ATL", "score": ""},
        "situation": {}, "leaders": [],
    }]
    sc_plugin.strip._last_build = 0.0
    assert sc_plugin._display_strip(), "live strip failed to draw"
    if sc_plugin._urgent_adopt:
        assert sc_plugin.strip._wait_for_background_build()
        assert sc_plugin._display_strip()
    assert sc_plugin.strip._clock_box, (
        "the scroll should carry its own clock once a live game has taken "
        "over the static panel, since the clock is no longer shown there"
    )
    assert sc_calls[-1] is True, (
        f"weather_show_current must be True once a live game has taken "
        f"over the static panel, same as show_clock: {sc_calls}"
    )
    print("PASS  the scroll only carries its own clock and current weather "
          "conditions while a live game has taken over the static panel")

    # The refresh gate: check for a live game every idle_interval (a
    # minute by default), and once ANY followed team actually is live --
    # not just whichever is pinned to the static panel -- drop to
    # live_interval (5s) so balls, strikes and score stay current
    # instead of sitting frozen for most of a minute. Also fast during
    # _followed_game_starting_soon(), so a game beginning near the end of
    # an idle wait is not missed for most of that wait either.
    class _RefreshSpyGames:
        def __init__(self, games_list, starting_soon=False, other_live=False):
            self._games_list = games_list
            self._starting_soon = starting_soon
            self._other_live_flag = other_live
            self.refresh_calls = []

        def games(self, state=None):
            if state is None:
                return self._games_list
            return [g for g in self._games_list if g.get("state") == state]

        def has_live(self):
            return any(g.get("state") == STATE_LIVE for g in self._games_list)

        def has_any_live(self):
            return self.has_live() or self._other_live_flag

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

    # update() dispatches the actual refresh to a background thread now --
    # see _dispatch_background_update's own docstring for why (the host
    # framework skips a plugin's display() entirely for as long as its
    # update() call is running, and the real refresh is genuine network
    # I/O) -- so every assertion here needs to wait for that dispatch to
    # actually finish before checking what it did, the same as any other
    # background-dispatch test in this file.
    update_plugin.games = _RefreshSpyGames([])
    update_plugin._last_update = time.time() - 10
    update_plugin.update()
    update_plugin._wait_for_background_update()
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
    update_plugin._wait_for_background_update()
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
    update_plugin._wait_for_background_update()
    assert update_plugin.games.refresh_calls == [True], (
        f"a game starting soon should also force a refresh past the ~5s "
        f"gate: {update_plugin.games.refresh_calls}"
    )

    # A live game outside the followed teams must get the same fast gate
    # too -- refresh() fetches followed teams and other-live games in one
    # call, not two independently-timed ones, so "live around the league"
    # needs has_any_live(), not has_live(), or it sits on the slow idle
    # cadence whenever no followed team happens to also be live.
    update_plugin.games = _RefreshSpyGames([], other_live=True)
    update_plugin._last_update = time.time() - 10
    update_plugin.update()
    update_plugin._wait_for_background_update()
    assert update_plugin.games.refresh_calls == [True], (
        f"an other-live game with no followed team live should also force "
        f"a refresh past the ~5s gate: {update_plugin.games.refresh_calls}"
    )
    print("PASS  update() checks for a live game every ~60s, then drops to "
          "~5s the moment any game is live anywhere or a followed team is "
          "about to start")

    # update() must return almost immediately even when the underlying
    # refresh is slow -- the whole reason it dispatches to a background
    # thread rather than calling games.refresh() inline. The host
    # framework skips a plugin's display() entirely for as long as its
    # update() call is running (confirmed against the real one: a normal
    # live-game refresh took ~1s on the Pi, freezing the panel for a
    # fifth of every live_interval), so update() itself being fast is the
    # actual point of this, not just an implementation detail.
    class _SlowRefreshGames(_RefreshSpyGames):
        def __init__(self, *a, delay=0.2, **kw):
            super().__init__(*a, **kw)
            self._delay = delay
            self.refresh_started = threading.Event()
            self.refresh_finished = threading.Event()

        def refresh(self, force=False):
            self.refresh_started.set()
            time.sleep(self._delay)
            super().refresh(force=force)
            self.refresh_finished.set()

    slow_games = _SlowRefreshGames([{
        "id": "live2", "league": "mlb", "state": STATE_LIVE,
        "home": {"abbr": "NYY"}, "away": {"abbr": "BOS"},
    }], delay=0.3)
    update_plugin.games = slow_games
    update_plugin._last_update = time.time() - 10
    call_start = time.time()
    update_plugin.update()
    call_elapsed = time.time() - call_start
    assert call_elapsed < 0.1, (
        f"update() should return almost immediately, not block on the "
        f"refresh itself: took {call_elapsed:.3f}s"
    )
    assert slow_games.refresh_calls == [], (
        f"the refresh should not have happened yet -- update() only just "
        f"dispatched it: {slow_games.refresh_calls}"
    )
    assert slow_games.refresh_finished.wait(2.0), (
        "the background refresh never completed at all"
    )
    assert slow_games.refresh_calls == [True], (
        f"the background refresh should have run exactly once by now: "
        f"{slow_games.refresh_calls}"
    )
    print(f"PASS  update() returns in {call_elapsed*1000:.1f}ms even when "
          f"the underlying refresh takes {slow_games._delay*1000:.0f}ms, "
          f"dispatched to a background thread instead of blocking")

    # Single-flight: update() firing again while a slow refresh is still
    # in progress must not start a second, overlapping one -- that would
    # only add more concurrent network load, not fix anything, on exactly
    # the slow-network day this exists for.
    busy_games = _SlowRefreshGames([{
        "id": "live3", "league": "mlb", "state": STATE_LIVE,
        "home": {"abbr": "NYY"}, "away": {"abbr": "BOS"},
    }], delay=0.3)
    update_plugin.games = busy_games
    update_plugin._last_update = time.time() - 10
    update_plugin.update()
    assert busy_games.refresh_started.wait(2.0), (
        "the first dispatch's refresh never started"
    )
    # The gate itself would normally block a second dispatch this soon
    # (well under live_interval), so back-date _last_update to prove the
    # *single-flight guard*, not the gate, is what's actually preventing
    # overlap here.
    update_plugin._last_update = time.time() - 10
    update_plugin.update()
    assert busy_games.refresh_finished.wait(2.0), (
        "the in-flight refresh never completed"
    )
    assert busy_games.refresh_calls == [True], (
        f"a second update() while one refresh was still in flight should "
        f"not have started an overlapping second refresh: "
        f"{busy_games.refresh_calls}"
    )
    print("PASS  a second update() while a background refresh is still in "
          "flight does not dispatch an overlapping second one")

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
                                 "third": True, "batter": "J.Soto",
                                 "pitcher": "G.Cole"}},
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
                                     {"u1": "Mon 8/11 7:05"})
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

    # _interval() must also speed up from other-live alone -- refresh()
    # fetches followed teams and other-live games in the same call, not on
    # two independently-timed schedules, so "live around the league" needs
    # the same fast timer a followed team's own live game gets, not the
    # slow idle one just because no followed team happens to also be live.
    assert gol._interval() == gol.live_interval, (
        f"an other-live game with no followed team live should still use "
        f"the fast timer: interval={gol._interval()}, "
        f"live={gol.live_interval}, idle={gol.idle_interval}"
    )
    assert gol_empty._interval() == gol_empty.idle_interval, (
        "with nothing live at all, the idle timer is still correct"
    )
    print("PASS  _interval() also speeds up from other-live alone, not "
          "just a followed team's own live game")

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

    nested_standings = {
        "children": [
            {"name": "American League", "children": [
                {"name": "AL East", "standings": {"entries": [
                    {"team": {"abbreviation": "NYY"}, "stats": [
                        {"type": "streak", "displayValue": "W2"},
                    ]},
                ]}},
            ]},
            {"name": "National League", "children": [
                {"name": "NL East", "standings": {"entries": [
                    {"team": {"abbreviation": "NYM"}, "stats": [
                        {"type": "streak", "displayValue": "L1"},
                    ]},
                ]}},
            ]},
        ]
    }
    nested_parsed = ESPNGamesSource._parse_standings(nested_standings)
    assert nested_parsed == {"NYY": "W2", "NYM": "L1"}, (
        f"nested conference→division standings must still flatten: "
        f"{nested_parsed}"
    )
    print("PASS  standings parsing walks nested conference/division children")

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

    import inspect as _inspect
    default_streak_interval = _inspect.signature(
        GamesManager.refresh_streaks).parameters["interval"].default
    assert default_streak_interval <= 300.0, (
        f"a streak sitting stale for up to 30 minutes after a game goes "
        f"final reads as wrong, not just slow -- default interval is "
        f"{default_streak_interval}s, expected 300s or less"
    )
    print(f"PASS  refresh_streaks() defaults to a {default_streak_interval:.0f}s "
          f"interval, not the old 30-minute one")

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
    assert any("Rivalry" in t for t in rival_texts), (
        f"a configured rival should flag the status with Rivalry: {rival_texts}"
    )
    rival_colour_calls = [c for c in rival_spy.calls if "Rivalry" in c[0]]
    assert rival_colour_calls[0][1] == StripRenderer.RIVALRY, (
        f"a rivalry status should draw in the RIVALRY colour: {rival_colour_calls}"
    )

    plain_img = Image.new("RGB", (150, 32), (0, 0, 0))
    plain_spy = _BannerSpyDraw(_StreakID.Draw(plain_img))
    rrival._draw_game(plain_img, plain_spy, 2, plain_final, win_font, win_row_h,
                      focus_abbr="NYY", rivals=["BOS"])
    plain_texts = [c[0] for c in plain_spy.calls]
    assert not any("Rivalry" in t for t in plain_texts), (
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
    fcol_weather = {"label": "Bayonne", "units": "F", "now_temp": 78,
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

    # The forecast row (Next Hours / 4 Day Forecast) must be centred under
    # its own header when the header is wider than the columns, not left
    # flush with it -- a header wider than one short column used to leave
    # all the slack on the right, the same slack-dumped-on-one-side mistake
    # this file already fixes vertically everywhere else. When the columns
    # are wider than the header instead, they stay flush-left, since there
    # is no slack to split. Checked by spying on both _draw_forecast_row's
    # own starting x (the header's position, whatever preceded it in the
    # weather block) and the x each real _draw_forecast_column call
    # receives, so the comparison is against where the header actually is
    # rather than an assumption about it.
    from PIL import ImageDraw as _CenterID
    rcenter = StripRenderer(FakeDisplay(192, 32), {}, log)
    row_start_calls = []
    col_calls = []
    orig_frow = rcenter._draw_forecast_row
    orig_fcol = rcenter._draw_forecast_column

    def _spy_frow(img, draw, x, *a, **kw):
        row_start_calls.append(x)
        return orig_frow(img, draw, x, *a, **kw)

    def _spy_fcol(draw, x, *a, **kw):
        if not kw.get("measure_only"):
            col_calls.append(x)
        return orig_fcol(draw, x, *a, **kw)

    rcenter._draw_forecast_row = _spy_frow
    rcenter._draw_forecast_column = _spy_fcol

    narrow_weather = {"now_temp": 78, "units": "F", "now_condition": "Clear",
                      "hourly": [{"name": "8P", "temp": 80}]}
    narrow_probe = _CenterID.Draw(Image.new("RGB", (1, 1)))
    narrow_font, narrow_row_h = rcenter._fit_font(narrow_probe, 4, rcenter.height)
    # The header draws in a font strictly smaller than the columns -- see
    # _draw_forecast_row's own docstring -- so this has to match that same
    # font selection, not the shared body font, or the two disagree on
    # header_w the same way production and a stale test would.
    _narrow_smaller = rcenter._smaller_font(narrow_probe, narrow_row_h)
    narrow_header_font = _narrow_smaller[0] if _narrow_smaller else narrow_font
    header_w = rcenter._measure(narrow_probe, "Next Hours", narrow_header_font)[0]

    rcenter._draw_weather(
        Image.new("RGB", (300, 32), (0, 0, 0)), _CenterID.Draw(
            Image.new("RGB", (300, 32), (0, 0, 0))),
        2, narrow_weather, narrow_font, narrow_row_h)
    assert len(row_start_calls) == 1 and len(col_calls) == 1, (
        row_start_calls, col_calls
    )
    row_x = row_start_calls[0]
    single_col_w = orig_fcol(
        _CenterID.Draw(Image.new("RGB", (1, 1))), 0,
        {"name": "8P", "temp": 80}, narrow_font, narrow_row_h, "F",
        content_top=narrow_row_h + 1, measure_only=True)
    assert single_col_w < header_w, (
        f"test setup error: expected the single column ({single_col_w}px) "
        f"to be narrower than the header ({header_w}px), or this proves "
        f"nothing about centering"
    )
    expected_shift = max(0, (header_w - single_col_w) // 2)
    assert col_calls[0] > row_x, (
        f"a single narrow column under a wide header should shift right "
        f"to centre, not sit flush at the header's own start: "
        f"column={col_calls[0]}, header={row_x}"
    )
    assert abs((col_calls[0] - row_x) - expected_shift) <= 1, (
        f"column should centre under the header: got shift "
        f"{col_calls[0] - row_x}px, expected ~{expected_shift}px "
        f"(header={header_w}px, column={single_col_w}px)"
    )

    # This fixture is 5 real hour/temp columns -- production never shows
    # more than 5 (`hourly[:5]`) -- against the same "Next Hours" header
    # used above. In the sandbox font these columns are reliably wider
    # than their own header, so the row should sit flush-left with no
    # shift. On real BDF fonts that isn't guaranteed, the same way it
    # wasn't for the 4-day forecast either -- a 1-2px
    # centering nudge either way is harmless on screen, so the assertion
    # checks the row obeys that same formula (whichever side is wider),
    # not a specific hardcoded outcome that only held for one font.
    wide_weather = {"now_temp": 78, "units": "F", "now_condition": "Clear",
                    "hourly": [{"name": "8P", "temp": 80}, {"name": "9P", "temp": 82},
                              {"name": "10P", "temp": 79}, {"name": "11P", "temp": 77},
                              {"name": "12A", "temp": 75}]}
    row_start_calls.clear()
    col_calls.clear()
    rcenter._draw_weather(
        Image.new("RGB", (300, 32), (0, 0, 0)), _CenterID.Draw(
            Image.new("RGB", (300, 32), (0, 0, 0))),
        2, wide_weather, narrow_font, narrow_row_h)
    wide_total_w = sum(
        orig_fcol(_CenterID.Draw(Image.new("RGB", (1, 1))), 0, entry,
                 narrow_font, narrow_row_h, "F",
                 content_top=narrow_row_h + 1, measure_only=True)
        for entry in wide_weather["hourly"]
    )
    expected_wide_shift = max(0, (header_w - wide_total_w) // 2)
    actual_wide_shift = col_calls[0] - row_start_calls[0]
    assert abs(actual_wide_shift - expected_wide_shift) <= 1, (
        f"forecast row's own centering formula not honoured: got shift "
        f"{actual_wide_shift}px, expected ~{expected_wide_shift}px "
        f"(header={header_w}px, columns={wide_total_w}px)"
    )
    print(f"PASS  forecast columns centre under a wider header "
          f"({single_col_w}px column under a {header_w}px header, shifted "
          f"{expected_shift}px), and obey the same centering formula "
          f"({wide_total_w}px columns under a {header_w}px header, "
          f"shifted {actual_wide_shift}px) whichever side ends up wider "
          f"on the font actually loaded")

    # _draw_forecast_row must return a width to add to x, the same
    # convention every other segment on the strip returns -- not the
    # absolute end position. Shipped broken once already: returning the
    # absolute position made the delta grow with wherever x already was,
    # inflating the whole weather segment's width by roughly double
    # whatever x was at the call site and leaving a large blank gap
    # before the next segment. Caught by calling it directly at two
    # different starting x values and checking the returned width is
    # identical either way -- a width genuinely independent of position,
    # which an absolute-position bug could not produce.
    row_probe = _CenterID.Draw(Image.new("RGB", (1, 1)))
    row_font, row_row_h = rcenter._fit_font(row_probe, 4, rcenter.height)
    row_entries = [{"name": "8P", "temp": 80}, {"name": "9P", "temp": 82}]
    width_at_2 = rcenter._draw_forecast_row(
        Image.new("RGB", (300, 32), (0, 0, 0)),
        _CenterID.Draw(Image.new("RGB", (300, 32), (0, 0, 0))),
        2, row_entries, "Next Hours", row_font, row_row_h, "F",
        content_top=row_row_h + 1)
    width_at_100 = rcenter._draw_forecast_row(
        Image.new("RGB", (300, 32), (0, 0, 0)),
        _CenterID.Draw(Image.new("RGB", (300, 32), (0, 0, 0))),
        100, row_entries, "Next Hours", row_font, row_row_h, "F",
        content_top=row_row_h + 1)
    assert width_at_2 == width_at_100, (
        f"_draw_forecast_row's return value must not depend on the "
        f"starting x -- got {width_at_2}px at x=2 and {width_at_100}px "
        f"at x=100, which means it's returning an absolute position "
        f"instead of a width"
    )
    assert width_at_2 < 100, (
        f"a two-column forecast row under 'Next Hours'-sized content "
        f"should not measure anywhere near 100px wide: {width_at_2}px"
    )
    print(f"PASS  _draw_forecast_row returns a width independent of its "
          f"starting x ({width_at_2}px at both x=2 and x=100)")

    # "Next Hours" used to sit at the same top margin the column's own
    # day/hour label independently anchored to, using a different (larger)
    # font -- header and content competing for the same rows rather than
    # one sitting above the other. The header's own text row must now end
    # (or at least not start later than) the row the column's label starts
    # on.
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
    header_weather = {"label": "Bayonne", "units": "F", "now_temp": 78,
                      "now_condition": "CLEAR", "alerts": [],
                      "hourly": [{"name": "8P", "temp": 86, "condition": "Sunny"},
                                {"name": "9P", "temp": 85, "condition": "Clear"}]}
    rheader._draw_weather(header_img, header_spy, 2, header_weather,
                         header_font, header_row_h)
    header_ys = [y for y, t in header_spy.calls if t == "Next Hours"]
    label_ys = [y for y, t in header_spy.calls if t in ("8P", "9P")]
    assert header_ys and label_ys, (
        f"expected both the header and an hour label to draw text: "
        f"{header_spy.calls}"
    )
    assert min(label_ys) > min(header_ys), (
        f"the hour label should start on a row strictly below the header, "
        f"not share its row: header_y={header_ys}, label_y={label_ys}"
    )
    print(f"PASS  'Next Hours' header sits above its columns' own hour "
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
    assert "Feels Like" in weather_src, "feels-like label was not spelled out"
    assert '"FL ' not in weather_src, "the old abbreviated form is still present"
    print("PASS  feels-like temperature is spelled out in full, not abbreviated")

    # Moon phase: pure arithmetic, no network -- illumination must stay in
    # 0..100, the name must be one of the eight real phases, and waxing must
    # agree with which half of the cycle the fraction actually falls in.
    import moon_phase
    from datetime import timedelta as _moon_td
    valid_names = {
        "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
        "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
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
    # Aware local wall times must convert to UTC, not strip tzinfo.
    from datetime import timezone as _tz
    aware_ref = _dt(2000, 1, 6, 13, 14, tzinfo=_tz(_moon_td(hours=-5)))
    aware_info = moon_phase.phase_info(aware_ref)
    assert abs(aware_info["fraction"] - ref_info["fraction"]) < 1e-9, (
        f"EST 13:14 on the reference day must match UTC 18:14: "
        f"{aware_info} vs {ref_info}"
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
    # install) drops the hourly column and the moon phase, but current
    # conditions and the 4-day forecast must still draw -- the same
    # reasoning that already keeps a weather warning up during a live game.
    forecast_weather = dict(moon_weather, hourly=[{"name": "8P", "temp": 77}],
                            daily=[{"name": "Mon", "temp": 82, "condition": "Sunny"},
                                   {"name": "Tue", "temp": 79, "condition": "Cloudy"},
                                   {"name": "Wed", "temp": 75, "condition": "Rain"},
                                   {"name": "Thu", "temp": 78, "condition": "Clear"}])
    # 2pm, deliberately inside the hourly-forecast's own 6am-8pm daytime
    # window (tested separately below) so this test is only about
    # show_forecast, not confounded by that other cutoff.
    shown_img = Image.new("RGB", (400, 32), (0, 0, 0))
    shown_draw = _MoonID.Draw(shown_img)
    shown_w = rmoonw._draw_weather(
        shown_img, shown_draw, 2, forecast_weather, moon_font, moon_row_h,
        _dt(2026, 8, 12, 14, 0), show_forecast=True)

    hidden_img = Image.new("RGB", (400, 32), (0, 0, 0))
    hidden_draw = _MoonID.Draw(hidden_img)
    hidden_w = rmoonw._draw_weather(
        hidden_img, hidden_draw, 2, forecast_weather, moon_font, moon_row_h,
        _dt(2026, 8, 12, 14, 0), show_forecast=False)
    assert hidden_w < shown_w, (
        f"show_forecast=False should drop the hourly forecast and moon "
        f"columns: shown={shown_w}px, hidden={hidden_w}px"
    )
    assert hidden_w > 0, (
        "current conditions should still draw with show_forecast=False"
    )
    # With daily present, hide must still leave the 4-day row -- wider than
    # current-conditions alone would be.
    no_daily = dict(moon_weather)
    bare_img = Image.new("RGB", (400, 32), (0, 0, 0))
    bare_w = rmoonw._draw_weather(
        bare_img, _MoonID.Draw(bare_img), 2, no_daily, moon_font, moon_row_h,
        _dt(2026, 8, 12, 14, 0), show_forecast=False)
    assert hidden_w > bare_w, (
        f"4-day forecast must stay up when show_forecast=False: "
        f"with daily={hidden_w}px, without={bare_w}px"
    )
    print(f"PASS  show_forecast=False hides the hourly forecast and moon "
          f"phase but keeps current conditions and 4-day forecast up "
          f"({shown_w}px -> {hidden_w}px; bare current={bare_w}px)")

    # show_current=False hides the icon and plain current-temperature
    # number -- whatever the static panel is already showing, nothing
    # live pinned there -- but feels-like is not shown on that panel at
    # all, so it stays up, using the icon+single-line treatment the
    # temperature would otherwise get rather than sitting paired under a
    # hidden number. A spy on the actual text draws is the direct check
    # here -- width alone can't distinguish "78F" from "Feels Like 85F".
    class _TextSpyDraw:
        def __init__(self, inner):
            self.inner = inner
            self.texts = []

        def text(self, xy, text, font=None, fill=None):
            self.texts.append(text)
            self.inner.text(xy, text, font=font, fill=fill)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    feels_weather = {"now_temp": 78, "now_feels": 85, "units": "F",
                     "now_condition": "Clear", "label": "Bayonne"}

    def _draw(weather, show_current):
        img = Image.new("RGB", (250, 32), (0, 0, 0))
        spy = _TextSpyDraw(_MoonID.Draw(img))
        w = rmoonw._draw_weather(img, spy, 2, weather, moon_font,
                                 moon_row_h, show_current=show_current)
        return spy.texts, w

    current_texts, current_w = _draw(feels_weather, True)
    feels_texts, feels_w = _draw(feels_weather, False)
    assert "78F" in current_texts and "Feels Like 85F" in current_texts, (
        f"show_current=True should draw both the plain temperature and "
        f"feels-like: {current_texts}"
    )
    assert "78F" not in feels_texts, (
        f"show_current=False must not draw the plain current temperature: "
        f"{feels_texts}"
    )
    assert "Feels Like 85F" in feels_texts, (
        f"show_current=False must still draw feels-like: {feels_texts}"
    )
    assert feels_w > 0
    print(f"PASS  show_current=False hides the plain current temperature "
          f"but still draws feels-like ({current_w}px with both -> "
          f"{feels_w}px feels-like only)")

    # With no feels-like data at all, show_current=False collapses to just
    # the header -- narrower than with show_current=True, and still
    # within the shared margin (nothing drawn out of bounds).
    plain_weather = {"now_temp": 78, "units": "F", "now_condition": "Clear",
                     "label": "Bayonne"}
    plain_texts, plain_w = _draw(plain_weather, False)
    _, plain_with_current_w = _draw(plain_weather, True)
    assert plain_texts == ["Bayonne"], (
        f"with no feels-like data, show_current=False should draw only "
        f"the header: {plain_texts}"
    )
    assert plain_w < plain_with_current_w, (
        f"header-only should measure narrower than showing current "
        f"conditions: plain={plain_w}px, with_current={plain_with_current_w}px"
    )
    plain_img = Image.new("RGB", (250, 32), (0, 0, 0))
    rmoonw._draw_weather(plain_img, _MoonID.Draw(plain_img), 2, plain_weather,
                         moon_font, moon_row_h, show_current=False)
    ppx = plain_img.load()
    lit = [y for y in range(32) for x in range(plain_w)
           if ppx[x, y] != (0, 0, 0)]
    assert lit and min(lit) >= 1 and max(lit) <= 30, (
        f"header-only fallback should still respect the shared 1px "
        f"top/bottom margin: rows {min(lit)}-{max(lit)}"
    )
    print(f"PASS  show_current=False with no feels-like data at all "
          f"collapses to just the header ({plain_w}px), still within margin")

    # The hourly ("Next Hours") column has its own separate 6am-8pm cutoff,
    # unrelated to show_forecast -- an hour-by-hour forecast stops earning
    # its place once it's mostly covering overnight. The moon phase is
    # unaffected either way.
    #
    # Isolated as with-hourly-data minus without, at the *same* moment each
    # time -- moon illumination is a continuous function of the exact
    # datetime passed, not just the date, so comparing raw widths across
    # different hours directly (as an earlier version of this test did)
    # picked up moon-phase text width drift alongside the hourly column,
    # and failed for the wrong reason. Holding `when` fixed within each
    # comparison cancels the moon out, leaving only the hourly column's
    # own contribution.
    baseline_weather = dict(forecast_weather)
    del baseline_weather["hourly"]

    def _hourly_contribution(when):
        with_hourly = rmoonw._draw_weather(
            Image.new("RGB", (250, 32), (0, 0, 0)), _MoonID.Draw(
                Image.new("RGB", (250, 32), (0, 0, 0))),
            2, forecast_weather, moon_font, moon_row_h, when,
            show_forecast=True)
        without_hourly = rmoonw._draw_weather(
            Image.new("RGB", (250, 32), (0, 0, 0)), _MoonID.Draw(
                Image.new("RGB", (250, 32), (0, 0, 0))),
            2, baseline_weather, moon_font, moon_row_h, when, show_forecast=True)
        return with_hourly - without_hourly

    day_delta = _hourly_contribution(_dt(2026, 8, 12, 14, 0))    # 2pm
    night_delta = _hourly_contribution(_dt(2026, 8, 12, 21, 0))  # 9pm
    early_delta = _hourly_contribution(_dt(2026, 8, 12, 5, 0))   # 5am
    open_delta = _hourly_contribution(_dt(2026, 8, 12, 6, 0))    # 6am, shown
    close_delta = _hourly_contribution(_dt(2026, 8, 12, 20, 0))  # 8pm, hidden

    assert day_delta > 0, (
        f"hourly forecast should contribute real width at 2pm: {day_delta}px"
    )
    assert night_delta == 0, (
        f"hourly forecast should be hidden by 9pm: {night_delta}px"
    )
    assert early_delta == 0, (
        f"hourly forecast should still be hidden at 5am, before the 6am "
        f"cutoff: {early_delta}px"
    )
    assert open_delta == day_delta, (
        f"6am itself should already show the hourly forecast: "
        f"{open_delta}px vs daytime {day_delta}px"
    )
    assert close_delta == 0, (
        f"8pm itself should already hide the hourly forecast: {close_delta}px"
    )
    print(f"PASS  hourly forecast column only shows 6am-8pm "
          f"(contributes {day_delta}px by day, {night_delta}px overnight), "
          f"moon unaffected")

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

    # The strip segment: a two-row block, "N Days" above "To <Name>" below,
    # "Today!" when it is actually the day, all within the shared margin.
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
    assert "Today!" in today_spy, (
        f"days=0 should read as Today!, not '0 Days': {today_spy}"
    )
    print(f"PASS  countdown strip segment renders within the shared margin "
          f"({cd_w}px) and reads Today! on the day itself")

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
    center_weather = {"label": "Bayonne", "units": "F", "now_temp": 78,
                      "now_feels": 85, "now_condition": "CLEAR", "alerts": []}
    center_team = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    center_final = {"id": "cf1", "league": "mlb", "state": STATE_FINAL,
                    "start": "",
                    "away": {"abbr": "NYY", "score": "4", "winner": False},
                    "home": {"abbr": "NYY", "score": "7", "winner": True},
                    "situation": {}, "leaders": [
                        {"team": "NYY", "name": "A.Judge", "line": "2-4, HR",
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
            {"rank": 1, "name": "Rafael Devers", "short_name": "R.Devers",
             "team": "BOS", "value": "38", "player_id": "d1"},
            {"rank": 2, "name": "Aaron Judge", "short_name": "A.Judge",
             "team": "NYY", "value": "35", "player_id": "j1"},
        ],
        ("battingAverage", "al"): [
            {"rank": 1, "name": "Aaron Judge", "short_name": "A.Judge",
             "team": "NYY", "value": ".312", "player_id": "j1"},
        ],
        ("runsBattedIn", "al"): [
            {"rank": 3, "name": "Ben Rice", "short_name": "B.Rice",
             "team": "NYY", "value": "70", "player_id": "r1"},
        ],
    }
    tmvp_stats = {"j1": {"AVG": ".312", "HR": "35", "RBI": "88"}}
    stub_leaders = StubLeadersManager(tmvp_rows, tmvp_stats)
    tmvp_awards = BaseballAwardsManager(log, stub_leaders)

    best = tmvp_awards.team_best("NYY", "al")
    assert best is not None and best["short_name"] == "A.Judge", (
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
    rtmvp._draw_team_mvp(tmvp_img, tmvp_draw, 2, "Aaron Judge", "A.Judge",
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
    rtmvp._draw_team_mvp(long_line_img, long_line_spy, 2, "Aaron Judge", "A.Judge",
                        "AVG .312  HR 35  RBI 88", tmvp_font, tmvp_row_h)
    assert "Aaron Judge" in long_line_spy.calls, (
        f"a stat line wider than the short name should show the full name: "
        f"{long_line_spy.calls}"
    )

    short_line_img = Image.new("RGB", (150, 32), (0, 0, 0))
    short_line_spy = _SpyDraw(_TmvpID.Draw(short_line_img))
    rtmvp._draw_team_mvp(short_line_img, short_line_spy, 2, "Aaron Judge", "A.Judge",
                        "HR 35", tmvp_font, tmvp_row_h)
    assert "A.Judge" in short_line_spy.calls and "Aaron Judge" not in short_line_spy.calls, (
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
        "name": "Aaron Judge", "short_name": "A.Judge",
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
    roster_names = {"h1": "A.Judge", "h2": "B.Bench", "h3": "C.Role",
                    "p1": "D.Swingman", "p2": "E.Ace"}
    roster_full_names = {"h1": "Aaron Judge", "h2": "Bobby Bench", "h3": "Charlie Role",
                          "p1": "Danny Swingman", "p2": "Eddie Ace"}
    roster_awards = BaseballAwardsManager(log, None)
    roster_best = roster_awards.team_mvp_from_roster(
        roster_hitting, roster_pitching, roster_names, roster_full_names)
    assert roster_best is not None and roster_best["short_name"] == "A.Judge", (
        f"clear statistical standout did not win the roster MVP: {roster_best}"
    )
    assert roster_best["name"] == "Aaron Judge", (
        f"team_mvp_from_roster() must also carry the full name: {roster_best}"
    )

    # Isolate pitching only, so the ERA-direction check cannot be masked by
    # the hitter winning on breadth or raw point volume regardless.
    pitching_only = roster_awards.team_mvp_from_roster(
        {}, roster_pitching, roster_names, roster_full_names)
    assert pitching_only is not None and pitching_only["short_name"] == "E.Ace", (
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
        "name": "Aaron Judge", "short_name": "A.Judge",
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
    lb_rows = [{"rank": 1, "short_name": "A.Judge", "team": "NYY", "value": "35"}]
    lb_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    lb_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    lb_plugin._leaderboards = lambda: (
        [("AL HR Leaders", lb_rows, "HR")], [("AL MVP Watch", lb_rows)])
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
        [("AL HR Leaders", lb_rows, "HR")], [("AL MVP Watch", lb_rows)])
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
        [("AL HR Leaders", lb_rows, "HR")], [("AL MVP Watch", lb_rows)])
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
        [("AL HR Leaders", lb_rows, "HR")], [("AL MVP Watch", lb_rows)])
    urgent_plugin.teams_leaderboards_on = True
    urgent_plugin.games._games = [{
        "id": "urg1", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYY", "score": ""}, "away": {"abbr": "BOS", "score": ""},
        "situation": {}, "leaders": [],
    }]
    assert urgent_plugin._display_strip(), "urgent-adopt baseline strip failed to draw"
    urgent_not_live_width = urgent_plugin.strip._strip_cache.width
    assert urgent_plugin._last_live_signature == ((), ())
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
    scroll_offset_before_adopt = urgent_plugin._scroll_offset
    # Usually still pending -- a real build takes measurably longer than
    # this one Python call -- but on a small enough strip the background
    # thread can occasionally finish and get adopted within this same
    # frame, which is only a faster win, not a failure: either timing is
    # correct, so only the *eventual* outcome is asserted below, not which
    # frame it landed on.
    if urgent_plugin._urgent_adopt:
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

    # The bug this was actually chasing: a game that was already live
    # stays live (state never flips), but the count, outs or batter
    # changes -- a new at-bat. has_any_live() alone never notices this,
    # since it only looks at *which* games are live, not their own
    # content, so a plain live/not-live transition check would leave
    # exactly this case waiting for the scroll seam. The fingerprint in
    # _live_signature() must catch it too.
    inplay_plugin = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    inplay_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"}])
    inplay_plugin.teams_panel_on = False
    inplay_plugin.games._games = [{
        "id": "inplay1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "1"}, "away": {"abbr": "BOS", "score": "0"},
        "situation": {"kind": "baseball", "balls": 0, "strikes": 0, "outs": 0},
        "leaders": [],
    }]
    assert inplay_plugin._display_strip(), "in-play baseline strip failed to draw"
    assert inplay_plugin._urgent_adopt is False, (
        "the very first build must not itself count as a change"
    )
    baseline_signature = inplay_plugin._last_live_signature

    # Same game, same state, next batter: outs and the count moved, the
    # score didn't. This is the "next batter has already been up and out
    # and hasn't updated" scenario reported against a long strip.
    inplay_plugin.games._games = [{
        "id": "inplay1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "1"}, "away": {"abbr": "BOS", "score": "0"},
        "situation": {"kind": "baseball", "balls": 1, "strikes": 2, "outs": 1},
        "leaders": [],
    }]
    inplay_plugin.strip._last_build = 0.0
    assert inplay_plugin._display_strip(), "in-play update frame failed to draw"
    assert inplay_plugin._last_live_signature != baseline_signature, (
        "the live signature must change when the count/outs change, even "
        "though the game's state never left STATE_LIVE"
    )
    inplay_offset_before_adopt = inplay_plugin._scroll_offset
    # As above: usually still pending by this point, but on a strip this
    # small the background build can occasionally finish and adopt within
    # the very same frame -- an even faster result, not a failure. Only
    # the eventual settled state is asserted, not which frame reached it.
    if inplay_plugin._urgent_adopt:
        assert inplay_plugin.strip._wait_for_background_build(), (
            "background rebuild for the in-play update did not finish"
        )
        assert inplay_plugin._display_strip(), "in-play follow-up frame failed to draw"
    assert inplay_plugin._urgent_adopt is False, (
        "the out-of-turn adopt should have settled by now, one way or "
        "the other"
    )
    assert inplay_offset_before_adopt < 5.0, (
        "this only proves anything if the scroll was still essentially at "
        f"the start, nowhere near a seam: offset was "
        f"{inplay_offset_before_adopt}px"
    )
    print("PASS  an already-live game's own count/outs/batter changing "
          "adopts immediately too, not just a game starting or ending")

    # The single most common live-game case: exactly one followed team,
    # its only game the one now pinned to the static panel. _display_strip
    # excludes a panel-pinned game from teams_and_games (showing it twice
    # would waste the scroll), which also made it invisible to
    # _live_signature -- a followed team's live game going from nothing
    # live to live (or its score/count changing) never registered as a
    # change at all in exactly this case, the one this plugin's whole
    # static-panel feature exists for. The panel itself always redraws
    # fresh every frame regardless, so this was never a stale-panel bug --
    # but leaderboards/awards/countdowns hiding, and the clock joining the
    # scroll, still silently waited for the next natural seam.
    # A second followed team with its own game keeps teams_and_games
    # non-empty once the panel absorbs the first team's only game entirely
    # -- with a single followed team, that absorption empties the scroll
    # outright and _display_strip bails out before ever reaching
    # _live_signature, which would prove nothing about this fix either way.
    panel_urgent = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [
            {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
            {"abbr": "NYM", "league": "mlb", "name": "Mets"},
        ]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    panel_urgent.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
        {"abbr": "NYM", "league": "mlb", "name": "Mets"},
    ])
    panel_urgent.teams_panel_priority = ["NYY"]
    panel_urgent._leaderboards = lambda: (
        [("AL HR Leaders", lb_rows, "HR")], [("AL MVP Watch", lb_rows)])
    panel_urgent.teams_leaderboards_on = True
    panel_urgent.games._games = [{
        "id": "pu1", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYY", "score": ""}, "away": {"abbr": "BOS", "score": ""},
        "situation": {}, "leaders": [],
    }, {
        "id": "pu3", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYM", "score": ""}, "away": {"abbr": "ATL", "score": ""},
        "situation": {}, "leaders": [],
    }]
    assert panel_urgent._display_strip(), "panel-urgent baseline strip failed to draw"
    assert panel_urgent._urgent_adopt is False

    panel_urgent.games._games = [{
        "id": "pu2", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "1"}, "away": {"abbr": "BOS", "score": "0"},
        "situation": {"kind": "baseball", "balls": 0, "strikes": 0, "outs": 0},
        "leaders": [],
    }, {
        "id": "pu3", "league": "mlb", "state": STATE_UPCOMING, "start": "",
        "home": {"abbr": "NYM", "score": ""}, "away": {"abbr": "ATL", "score": ""},
        "situation": {}, "leaders": [],
    }]
    panel_urgent.strip._last_build = 0.0
    assert panel_urgent._display_strip(), "panel-urgent trigger frame failed to draw"
    live_signature_after = panel_urgent._last_live_signature
    assert live_signature_after != ((), ()), (
        "a followed team's live game becoming the panel-pinned game must "
        "still register in the live-content fingerprint, even though it "
        "is excluded from teams_and_games to avoid showing it twice"
    )
    # As in the tests above: usually still pending at this exact point,
    # but on a strip this small the background build can occasionally
    # finish and adopt within the same frame -- a faster result, not a
    # failure. Only the eventual settled state matters here.
    if panel_urgent._urgent_adopt:
        assert panel_urgent.strip._wait_for_background_build()
        assert panel_urgent._display_strip()
    assert panel_urgent._urgent_adopt is False, (
        "the out-of-turn adopt should have settled by now, one way or "
        "the other"
    )
    print("PASS  a followed team's live game pinned to the static panel "
          "still triggers the same out-of-turn adopt as any other live "
          "game, not just ones that stay in the scroll")

    # ---- 6. Plugin Lifecycle -------------------------------------------
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
        _manifest = _json.load(_mf)
    declared = _manifest["display_modes"]
    assert declared == ["local_scoreboard"], f"manifest declares dead modes: {declared}"
    assert plugin.get_available_modes() == ["local_scoreboard"]
    assert plugin.display("local_scoreboard") is True
    print("PASS  one declared mode, so no rotation slot can stall")

    # LEDMatrix's own scheduler only calls a plugin's update() as often as
    # the manifest's own "update_interval" says -- confirmed by reading
    # plugin_manager.py directly, where an unset value falls back to a
    # hardcoded 60s regardless of what this plugin's own live_interval
    # wants. This was set once already, then removed in a past version on
    # the mistaken belief that it duplicated idle_interval/live_interval's
    # job -- it doesn't: those decide what THIS plugin does once update()
    # runs, update_interval decides whether update() runs at all. Losing
    # it silently capped every refresh (live scores, streaks, weather) at
    # once a minute no matter how urgently the plugin's own logic wanted
    # to run sooner, and nothing in this offline suite could catch that,
    # since it is entirely the host framework's behavior -- confirmed
    # missing by reading the real framework source and the real device's
    # own logs, which showed refreshes a full minute apart during a live
    # game despite live_interval=5.
    assert "update_interval" in _manifest, (
        "update_interval must stay set in the manifest -- without it "
        "every refresh is capped at the framework's own 60s default, "
        "regardless of idle_interval/live_interval"
    )
    assert _manifest["update_interval"] <= 5, (
        f"update_interval must stay well under live_interval's default "
        f"(5s) or a live game's own updates cannot arrive any faster "
        f"than this: {_manifest['update_interval']}"
    )
    print(f"PASS  manifest declares update_interval={_manifest['update_interval']}s "
          f"so the framework calls update() often enough for live_interval "
          f"to actually govern the real refresh cadence")

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

    # Single followed team whose only game is pinned to the static panel:
    # teams_and_games used to go empty and _display_strip returned False
    # without calling draw_strip, so the panel never painted -- even when
    # other-live games were available for the scroll.
    panel_only = LocalScoreboardPlugin(
        "local-scoreboard", {"teams": [
            {"abbr": "NYG", "league": "nfl", "name": "Giants", "rivals": ["DAL"]},
        ],
         "static_panel": {"enabled": True, "width": 64, "priority": ["NYG"]},
         "other_live_games": {"enabled": True, "limit": 5}},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    panel_only.games = GamesManager(log, teams=[
        {"abbr": "NYG", "league": "nfl", "name": "Giants"},
    ])
    panel_only.teams_panel_on = True
    panel_only.teams_panel_width = 64
    panel_only.teams_panel_priority = ["NYG"]
    panel_only.teams_other_live_on = True
    panel_only.teams_other_live_limit = 5
    panel_only.games._games = [{
        "id": "po1", "league": "nfl", "state": STATE_LIVE, "start": "",
        "status_detail": "Q2 3:12",
        "home": {"abbr": "NYG", "score": "14"},
        "away": {"abbr": "PHI", "score": "10"},
        "situation": {"kind": "football", "down_distance": "2nd & 5",
                      "yard_line": "PHI 28", "possession": "NYG",
                      "red_zone": False, "clock": "3:12"},
        "leaders": [],
    }, {
        "id": "po2", "league": "nfl", "state": STATE_LIVE, "start": "",
        "status_detail": "Q1 8:00",
        "home": {"abbr": "SEA", "score": "7"},
        "away": {"abbr": "SF", "score": "3"},
        "situation": {"kind": "football", "down_distance": "1st & 10",
                      "yard_line": "SF 25", "possession": "SEA",
                      "red_zone": False, "clock": "8:00"},
        "leaders": [],
    }]
    assert panel_only._display_strip(), (
        "panel-pinned only followed live game must still draw (panel + "
        "other-live on the scroll), not return False with nothing painted"
    )
    assert panel_only.strip._static_panel is not None
    assert panel_only.display_manager.frames, (
        "draw_strip must have pushed a frame when the panel is the only "
        "followed content"
    )
    print("PASS  panel-only followed live still paints (with other-live "
          "on the scroll)")

    # Leaders team map: a failed first fetch must not freeze empty forever.
    from leaders_data_source import MLBStatsLeadersSource
    src_teams = MLBStatsLeadersSource(log)
    src_teams.session = type("S", (), {
        "get": staticmethod(lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("boom"))),
    })()
    src_teams._load_teams()
    assert src_teams._team_abbrs is None, (
        "failed team-map fetch must leave _team_abbrs as None so the next "
        f"call retries, not a sticky empty dict: {src_teams._team_abbrs!r}"
    )
    class _OkTeamsResp:
        def raise_for_status(self): pass
        def json(self):
            return {"teams": [{"id": 147, "abbreviation": "NYY"}]}
    src_teams.session = type("S", (), {
        "get": staticmethod(lambda *a, **k: _OkTeamsResp()),
    })()
    assert src_teams._team_abbr(147) == "NYY"
    print("PASS  leaders team-map retries after a failed first fetch")

    # Forecast/hourly temps must honor configured units (station obs already did).
    from weather_source import NWSWeather
    assert NWSWeather._convert_temp(77, "F", "C") == 25
    assert NWSWeather._convert_temp(25, "C", "F") == 77
    assert NWSWeather._convert_temp(77, "F", "F") == 77
    wx = NWSWeather(log, 40.66, -74.11, units="C")
    wx._grid = {
        "forecast": "https://example/forecast",
        "hourly": "https://example/hourly",
        "stations": "",
        "city": "Bayonne",
    }
    def _wx_get(url, params=None):
        if url.endswith("/forecast") or "forecast" in url and "Hourly" not in url:
            return {"properties": {"periods": [
                {"name": "Today", "shortForecast": "Sunny",
                 "temperature": 77, "temperatureUnit": "F"},
                {"name": "Tonight", "shortForecast": "Clear",
                 "temperature": 59, "temperatureUnit": "F"},
            ]}}
        if "hourly" in url.lower() or url.endswith("/hourly"):
            return {"properties": {"periods": [
                {"startTime": "2026-08-15T20:00:00-04:00",
                 "temperature": 72, "temperatureUnit": "F",
                 "shortForecast": "Clear"},
            ]}}
        return None
    wx._get = _wx_get
    wx._fetch_current = lambda grid: {}
    wx._fetch_alerts = lambda: []
    got = wx.fetch()
    assert got["temp"] == 25 and got["temp_unit"] == "C", got
    assert got["next_temp"] == 15, got
    assert got["hourly"][0]["temp"] == 22, got["hourly"]
    print("PASS  weather forecast/hourly temps convert when units=C")

    # Kid-friendly weather tips, football jargon, and win cheer.
    assert StripRenderer.weather_kid_tip(
        {"now_temp": 40, "units": "F", "now_condition": "Clear"}) == "Jacket!"
    assert StripRenderer.weather_kid_tip(
        {"now_temp": 70, "units": "F", "now_condition": "Rain"}) == "Umbrella!"
    assert StripRenderer.weather_kid_tip(
        {"now_temp": 72, "units": "F", "now_condition": "Sunny"}) == "Nice Day!"
    rkid = StripRenderer(FakeDisplay(192, 32), {"kid_friendly": True}, log)
    assert rkid.kid_friendly
    kimg = Image.new("RGB", (200, 32), (0, 0, 0))
    from PIL import ImageDraw as _KidID
    kdraw = _TextSpyDraw(_KidID.Draw(kimg))
    kfont, krow = rkid._fit_font(kdraw, 3, 32)
    nfl_kid = {
        "id": "k1", "league": "nfl", "state": STATE_LIVE,
        "home": {"abbr": "NYG", "score": "14"},
        "away": {"abbr": "DAL", "score": "10"},
        "situation": {"kind": "football", "down_distance": "3rd & 7",
                      "yard_line": "DAL 40", "possession": "NYG",
                      "red_zone": False, "clock": "5:00"},
        "leaders": [],
    }
    rkid._draw_live_detail(kimg, kdraw, 2, nfl_kid, kfont, krow)
    joined = " ".join(str(t) for t in kdraw.texts)
    assert "NYG Ball" in joined and "3rd Down" in joined, joined
    assert "DAL 40" not in joined and "3rd & 7" not in joined, joined
    win_game = {
        "id": "w1", "league": "mlb", "state": STATE_FINAL, "start": "",
        "home": {"abbr": "NYY", "name": "Yankees", "score": "5", "winner": True},
        "away": {"abbr": "BOS", "name": "Red Sox", "score": "3", "winner": False},
        "situation": {}, "leaders": [],
    }
    assert StripRenderer._followed_side_won(win_game, "NYY")["abbr"] == "NYY"
    cheer_w = rkid._draw_win_cheer(kimg, kdraw, 2, "Yankees", kfont, krow)
    assert cheer_w > 10 and any("Win!" in str(t) for t in kdraw.texts)
    print("PASS  kid-friendly weather tips, football simplify, win cheer")

    # Favorite player: prefer Yamal in scorers; Star note draws the name.
    yamal_game = {
        "id": "bar1", "league": "laliga", "state": STATE_FINAL,
        "home": {"abbr": "BAR", "score": "3", "winner": True},
        "away": {"abbr": "RMA", "score": "1", "winner": False},
        "leaders": [
            {"team": "BAR", "name": "R.Lewandowski", "full_name": "Robert Lewandowski",
             "line": "12'", "category": "GOAL", "side": "batting"},
            {"team": "BAR", "name": "L.Yamal", "full_name": "Lamine Yamal",
             "line": "67'", "category": "GOAL", "side": "batting"},
        ],
    }
    picked = ESPNGamesSource.pick_performer(
        yamal_game, "BAR", prefer_name="Lamine Yamal")
    assert picked and "Yamal" in picked["full_name"], picked
    rfav = StripRenderer(FakeDisplay(192, 32), {"kid_friendly": True}, log)
    fimg = Image.new("RGB", (160, 32), (0, 0, 0))
    fspy = _TextSpyDraw(_KidID.Draw(fimg))
    ffont, frow = rfav._fit_font(fspy, 3, 32)
    rfav._draw_favorite_player(fimg, fspy, 2, "Lamine Yamal", ffont, frow)
    assert any("Star" in str(t) for t in fspy.texts)
    assert any("Lamine Yamal" in str(t) for t in fspy.texts)
    print("PASS  favorite_player prefers Yamal and draws a Star note")

    # Original fun-art bumpers (not licensed characters) show under kid mode.
    import kid_art as _kid_art
    assert set(_kid_art.SPRITE_ORDER) <= set(_kid_art.SPRITES)
    picks = _kid_art.pick_sprites(15, count=2)
    assert len(picks) == 2 and picks[0] != picks[1]
    rfun = StripRenderer(
        FakeDisplay(192, 32),
        {"kid_friendly": True, "fun_art": {"enabled": True, "count": 2}},
        log,
    )
    assert rfun._fun_art_enabled()
    assert rfun._fun_art_picks(type("C", (), {"hour": 15})()) == picks
    fimg2 = Image.new("RGB", (120, 32), (0, 0, 0))
    fdraw2 = _KidID.Draw(fimg2)
    ffont2, frow2 = rfun._fit_font(fdraw2, 3, 32)
    bw = rfun._draw_fun_bumper(fimg2, fdraw2, 2, "rocket", ffont2, frow2)
    lit = sum(1 for y in range(32) for x in range(min(80, bw + 2))
              if fimg2.getpixel((x, y)) != (0, 0, 0))
    assert bw > 20 and lit > 30, (bw, lit)
    # Animation: motion changes over time; refresh_fun_art repaints in place.
    d0 = _kid_art.motion("ball", 0.0)
    d1 = _kid_art.motion("ball", 0.4)
    assert d0 != d1, (d0, d1)
    team_a = {"abbr": "NYY", "league": "mlb", "name": "Yankees"}
    g_a = {
        "id": "fun1", "league": "mlb", "state": STATE_FINAL, "start": "",
        "home": {"abbr": "NYY", "score": "5", "winner": True},
        "away": {"abbr": "BOS", "score": "3", "winner": False},
        "situation": {}, "leaders": [],
    }
    with_fun = rfun.build_strip([(team_a, [g_a])])
    assert rfun._fun_art_regions, "fun bumpers must register animate regions"
    before = with_fun.copy()
    rfun.refresh_fun_art(10.0)
    rfun.refresh_fun_art(10.25)  # new 20Hz tick
    changed = sum(
        1 for y in range(before.height) for x in range(before.width)
        if before.getpixel((x, y)) != with_fun.getpixel((x, y))
    )
    assert changed > 0, "refresh_fun_art should move sprite pixels"
    # Wreckage: cracks/debris should light pixels beyond a static sprite.
    wreck_img = Image.new("RGB", (40, 32), (0, 0, 0))
    wreck_draw = _KidID.Draw(wreck_img)
    _kid_art.draw_wreckage(wreck_draw, 0, 40, 32, "dino", 1.25, 20, 16)
    wreck_lit = sum(1 for y in range(32) for x in range(40)
                    if wreck_img.getpixel((x, y)) != (0, 0, 0))
    assert wreck_lit > 8, wreck_lit
    roff = StripRenderer(
        FakeDisplay(192, 32),
        {"kid_friendly": True, "fun_art": {"enabled": False}},
        log,
    )
    without_fun = roff.build_strip([(team_a, [g_a])])
    assert with_fun.width > without_fun.width, (
        with_fun.width, without_fun.width)
    assert len(rfun._fun_art_regions) >= 2, rfun._fun_art_regions
    # Whole-panel chaos: cracks / glitch tears / interrupt on the final frame.
    chaos = Image.new("RGB", (192, 32), (40, 80, 120))
    for px in range(192):
        for py in range(32):
            chaos.putpixel((px, py), (40 + (px % 20), 80, 120))
    before_c = chaos.copy()
    phases = set()
    for tt in (1.0, 4.5, 7.0, 8.5):
        frame = before_c.copy()
        phases.add(_kid_art.apply_screen_chaos(frame, tt))
        assert any(
            frame.getpixel((x, y)) != before_c.getpixel((x, y))
            for y in range(32) for x in range(0, 192, 4)
        ), tt
    assert phases >= {"cracks", "glitch", "interrupt", "smash"}, phases
    assert _kid_art.funny_gag(0) in _kid_art.FUNNY_GAGS
    assert _kid_art.funny_gag(25) != _kid_art.funny_gag(0)
    print("PASS  kid fun-art bumpers draw, animate, and widen the strip")

    # Daily condensation: daytime highs only, Title Case labels, unit convert.
    wx_daily = NWSWeather(log, 40.66, -74.11, units="C")
    condensed = wx_daily._condense_daily([
        {"isDaytime": True, "name": "Monday", "temperature": 77,
         "temperatureUnit": "F", "shortForecast": "Sunny"},
        {"isDaytime": False, "name": "Monday Night", "temperature": 59,
         "temperatureUnit": "F", "shortForecast": "Clear"},
        {"isDaytime": True, "name": "Tuesday", "temperature": 80,
         "temperatureUnit": "F", "shortForecast": "Cloudy"},
    ], days=4)
    assert len(condensed) == 2, condensed
    assert condensed[0]["name"] == "Mon" and condensed[0]["temp"] == 25, condensed
    assert condensed[0]["low"] == 15, condensed
    assert condensed[1]["name"] == "Tue" and condensed[1]["temp"] == 27, condensed
    assert condensed[1].get("low") is None, condensed
    print("PASS  daily forecast condenses daytime highs with unit convert")

    # NFL/NBA: scoreboard RAT alone must not block a summary enrich, and an
    # empty miss must not stamp the final forever.
    gperf = GamesManager(log, teams=[
        {"abbr": "NYG", "league": "nfl", "name": "Giants"},
    ])
    gperf.fetch_leaders = True
    gperf.leaders_per_game = 2
    gperf._games = [{
        "id": "nfl-final", "league": "nfl", "state": STATE_FINAL, "start": "",
        "home": {"abbr": "NYG", "score": "24", "winner": True},
        "away": {"abbr": "DAL", "score": "17", "winner": False},
        "situation": {}, "leaders": [
            {"team": "NYG", "name": "D.Jones", "line": "rating",
             "category": "", "side": "batting"},
        ],
    }]
    class _NflLeaders:
        def fetch_batting(self, *a, **k):
            return []
        def fetch_leaders(self, league, event_id, per_game=2):
            return [{
                "team": "NYG", "name": "D.Jones",
                "line": "24-31, 305 YDS, 3 TD",
                "category": "PASS", "side": "batting",
            }]
    gperf.source = _NflLeaders()
    gperf._refresh_leaders()
    cats = {l.get("category") for l in gperf._games[0]["leaders"]}
    assert "PASS" in cats, gperf._games[0]["leaders"]
    assert f"nfl:nfl-final" in gperf._leaders_fetched
    # Empty miss must leave the key unstamped so the next refresh retries.
    gmiss = GamesManager(log, teams=[{"abbr": "NYK", "league": "nba", "name": "Knicks"}])
    gmiss.fetch_leaders = True
    gmiss._games = [{
        "id": "nba-final", "league": "nba", "state": STATE_FINAL, "start": "",
        "home": {"abbr": "NYK", "score": "100"}, "away": {"abbr": "BOS", "score": "98"},
        "situation": {}, "leaders": [],
    }]
    class _EmptyLeaders:
        def fetch_batting(self, *a, **k): return []
        def fetch_leaders(self, *a, **k): return []
    gmiss.source = _EmptyLeaders()
    gmiss._refresh_leaders()
    assert "nba:nba-final" not in gmiss._leaders_fetched
    print("PASS  NFL/NBA performer enrich merges summary lines; empty miss retries")

    # Partial league fetch failure must not wipe other leagues' good data.
    gpartial = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
        {"abbr": "NYK", "league": "nba", "name": "Knicks"},
    ])
    gpartial._games = [{
        "id": "keep-mlb", "league": "mlb", "state": STATE_FINAL, "start": "",
        "home": {"abbr": "NYY", "score": "5"}, "away": {"abbr": "BOS", "score": "3"},
        "situation": {}, "leaders": [],
    }, {
        "id": "old-nba", "league": "nba", "state": STATE_FINAL, "start": "",
        "home": {"abbr": "NYK", "score": "99"}, "away": {"abbr": "BOS", "score": "90"},
        "situation": {}, "leaders": [],
    }]
    class PartialStub:
        def fetch_scoreboard(self, league, **kwargs):
            if league == "mlb":
                return None  # request failed
            if league == "nba":
                return [{
                    "id": "new-nba", "league": "nba", "state": STATE_LIVE,
                    "start": "", "home": {"abbr": "NYK", "score": "10"},
                    "away": {"abbr": "BOS", "score": "8"},
                    "situation": {}, "leaders": [],
                }]
            return []
        def fetch_leaders(self, *a, **k): return []
        def fetch_batting(self, *a, **k): return []
    gpartial.source = PartialStub()
    gpartial.fetch_leaders = False
    gpartial.refresh(force=True)
    ids = {g["id"] for g in gpartial._games}
    assert "keep-mlb" in ids, f"failed MLB fetch wiped prior Yankees game: {ids}"
    assert "new-nba" in ids, f"successful NBA fetch did not land: {ids}"
    assert "old-nba" not in ids, f"stale NBA final should have been replaced: {ids}"
    print("PASS  partial league fetch failure keeps prior games for that league")

    # Far-future throttle must not stamp on a failed fetch (None).
    gfar = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
        {"abbr": "NYK", "league": "nba", "name": "Knicks"},
    ])
    far_tries = []
    class FarFailStub:
        def fetch_scoreboard(self, league, days_back=1, days_forward=7):
            if days_forward > 7:
                far_tries.append(league)
                return None
            if league == "mlb":
                return [{
                    "id": "mlb1", "league": "mlb", "state": STATE_FINAL,
                    "start": "", "home": {"abbr": "NYY", "score": "5"},
                    "away": {"abbr": "BOS", "score": "3"},
                    "situation": {}, "leaders": [],
                }]
            return []
        def fetch_leaders(self, *a, **k): return []
        def fetch_batting(self, *a, **k): return []
    gfar.source = FarFailStub()
    gfar.fetch_leaders = False
    # Call the wide lookup directly -- refresh() only reaches it when at
    # least one followed game already exists in the normal window.
    gfar._games = [{
        "id": "mlb1", "league": "mlb", "state": STATE_FINAL, "start": "",
        "home": {"abbr": "NYY", "score": "5"}, "away": {"abbr": "BOS", "score": "3"},
        "situation": {}, "leaders": [],
    }]
    gfar._find_far_future_games(gfar._team_index(), gfar._games)
    assert "nba" not in gfar._next_game_checked, (
        "failed far-future lookup must not start the 24h throttle"
    )
    assert far_tries == ["nba"], far_tries
    gfar._find_far_future_games(gfar._team_index(), gfar._games)
    assert far_tries == ["nba", "nba"], (
        f"failed far-future lookup should retry, not wait 24h: {far_tries}"
    )
    print("PASS  far-future lookup retries after failure instead of 24h throttle")

    # Refresh gate: in-flight no-op must not burn _last_update.
    gate_plugin = LocalScoreboardPlugin(
        "local-scoreboard",
        {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    )
    gate_plugin.games = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
    ])
    gate_plugin.games._games = [{
        "id": "g1", "league": "mlb", "state": STATE_LIVE, "start": "",
        "home": {"abbr": "NYY", "score": "1"}, "away": {"abbr": "BOS", "score": "0"},
        "situation": {}, "leaders": [],
    }]
    gate_plugin.games._fetched_at = time.time()
    gate_plugin._refresh_in_flight = True
    before = time.time() - 100
    gate_plugin._last_update = before
    gate_plugin.teams_live_interval = 5
    gate_plugin.update()
    assert gate_plugin._last_update == before, (
        "in-flight refresh must not advance _last_update (would double the "
        f"live interval): was {before}, now {gate_plugin._last_update}"
    )
    gate_plugin._refresh_in_flight = False
    gate_plugin.update()
    assert gate_plugin._last_update > before, (
        "a real dispatch must still advance _last_update"
    )
    gate_plugin._wait_for_background_update(timeout=2.0)
    print("PASS  refresh gate does not burn the interval on an in-flight no-op")

    # ESPN↔StatsAPI abbr aliases for streaks and roster id lookup.
    gstreak = GamesManager(log, teams=[{"abbr": "ARI", "league": "mlb"}])
    gstreak._streaks = {"AZ": "W4"}
    assert gstreak.streak_for({"abbr": "ARI"}) == "W4", (
        "streak_for must resolve ARI via AZ alias"
    )
    print("PASS  streak_for resolves ESPN/StatsAPI abbreviation aliases")

    src_alias = MLBStatsLeadersSource(log)
    src_alias._team_abbrs = {109: "AZ"}
    src_alias._team_ids = {"AZ": 109}
    assert src_alias._team_id("ARI") == 109, (
        "roster lookup must resolve ARI to StatsAPI AZ id"
    )
    print("PASS  leaders _team_id resolves ESPN abbr aliases to StatsAPI ids")

    info_ver = LocalScoreboardPlugin(
        "local-scoreboard",
        {"teams": [{"abbr": "NYY", "league": "mlb", "name": "Yankees"}]},
        FakeDisplay(192, 32), FakeCache(), None,
    ).get_info()["version"]
    man_ver = json.load(open(os.path.join(
        os.path.dirname(__file__), "manifest.json")))["version"]
    assert info_ver == man_ver, (
        f"get_info version {info_ver!r} must match manifest {man_ver!r}"
    )
    print(f"PASS  get_info version matches manifest ({man_ver})")

    # Other-live density filters.
    gdense = GamesManager(log, teams=[
        {"abbr": "NYY", "league": "mlb", "name": "Yankees"},
    ])
    gdense._other_live = [
        {"id": "m1", "league": "mlb", "state": STATE_LIVE,
         "home": {"abbr": "BOS"}, "away": {"abbr": "TOR"}},
        {"id": "n1", "league": "nfl", "state": STATE_LIVE,
         "home": {"abbr": "DAL"}, "away": {"abbr": "SEA"}},
        {"id": "n2", "league": "nfl", "state": STATE_LIVE,
         "home": {"abbr": "KC"}, "away": {"abbr": "BUF"}},
    ]
    only_mlb = gdense.other_live_games(
        limit=10, followed_leagues_only=True, per_league_limit=0)
    assert [g["id"] for g in only_mlb] == ["m1"], only_mlb
    capped = gdense.other_live_games(
        limit=10, followed_leagues_only=False, per_league_limit=1)
    assert len(capped) == 2 and {g["league"] for g in capped} == {"mlb", "nfl"}, capped
    print("PASS  other-live followed_leagues_only and per_league_limit filter")

    # Rivalry live boost duplicates the live rivalry card on the strip.
    rboost = StripRenderer(FakeDisplay(192, 32), {}, log)
    team_boost = {"abbr": "NYG", "league": "nfl", "name": "Giants",
                  "rivals": ["DAL"]}
    rival_live = {
        "id": "rb1", "league": "nfl", "state": STATE_LIVE, "start": "",
        "status_detail": "Q2", "away": {"abbr": "DAL", "score": "14"},
        "home": {"abbr": "NYG", "score": "10"},
        "situation": {"kind": "football", "down_distance": "1st & 10",
                      "yard_line": "DAL 40", "possession": "NYG",
                      "red_zone": False},
        "leaders": [],
    }
    strip0 = rboost.build_strip([(team_boost, [rival_live])], rivalry_live_boost=0)
    strip1 = StripRenderer(FakeDisplay(192, 32), {}, log).build_strip(
        [(team_boost, [rival_live])], rivalry_live_boost=1)
    assert strip1.width > strip0.width, (
        f"rivalry_live_boost=1 should widen the strip: "
        f"{strip0.width} -> {strip1.width}"
    )
    print("PASS  rivalry_live_boost widens the strip for live rivalry games")

    # NFL live strip + static panel regression goldens (ink / width checks).
    golden_dir = os.path.join(os.path.dirname(__file__), "test", "golden")
    os.makedirs(golden_dir, exist_ok=True)
    nfl_live = {
        "id": "dal-sea", "league": "nfl", "state": STATE_LIVE, "period": 2,
        "status_detail": "Q2 5:43", "start": "",
        "away": {"abbr": "DAL", "score": "14"},
        "home": {"abbr": "SEA", "score": "7"},
        "situation": {"kind": "football", "down_distance": "1st & 10",
                      "yard_line": "SEA 35", "possession": "DAL",
                      "red_zone": False, "clock": "5:43"},
        "leaders": [],
    }
    rgold = StripRenderer(FakeDisplay(192, 32), {}, log)
    gimg = Image.new("RGB", (400, 32), (0, 0, 0))
    from PIL import ImageDraw as _GID
    gdraw = _GID.Draw(gimg)
    gfont, grow = rgold._fit_font(gdraw, 3, 32)
    gw = rgold._draw_game(gimg, gdraw, 2, nfl_live, gfont, grow)
    gink = 0
    gpx = gimg.load()
    for _x in range(gimg.width):
        for _y in range(32):
            if sum(gpx[_x, _y]) > 30:
                gink = _x
    assert gw + 2 >= gink, (
        f"NFL live strip game undercounts width: returned {gw}, ink {gink}"
    )
    crop = gimg.crop((0, 0, gw + 4, 32))
    crop.save(os.path.join(golden_dir, "nfl_other_live_192x32.png"))
    panel = rgold.render_static_panel(nfl_live, "DAL", 64)
    assert panel is not None
    panel.save(os.path.join(golden_dir, "nfl_static_panel_64x32.png"))
    print(f"PASS  NFL live strip/panel regression goldens written to {golden_dir}")

    # Roster refresh must not stamp the throttle on a failed network call,
    # or a transient blip blanks the roster for the full cache_duration.
    from leaders_manager import BaseballLeadersManager as _LM
    lm_roster = _LM(log, cache_duration=3600)
    class _BoomRoster:
        def fetch_team_roster(self, abbr):
            raise RuntimeError("network down")
    lm_roster.data_source = _BoomRoster()
    lm_roster.refresh_team_roster("NYY")
    assert "NYY" not in lm_roster._roster_fetched_at, (
        "failed roster fetch must not stamp _roster_fetched_at"
    )
    print("PASS  roster refresh does not throttle after a failed fetch")

    # Soccer scorers from summary keyEvents.
    soccer_summary = {
        "header": {"competitions": [{"competitors": [
            {"team": {"id": "83", "abbreviation": "BAR"}},
            {"team": {"id": "86", "abbreviation": "RMA"}},
        ]}]},
        "keyEvents": [
            {"scoringPlay": False, "type": {"text": "Kickoff"}, "participants": []},
            {"scoringPlay": True, "type": {"text": "Goal"},
             "clock": {"displayValue": "23'"},
             "team": {"id": "83"},
             "participants": [{"athlete": {"displayName": "Robert Lewandowski"}}]},
            {"scoringPlay": True, "type": {"text": "Goal"},
             "clock": {"displayValue": "67'"},
             "team": {"id": "86"},
             "participants": [{"athlete": {"displayName": "Kylian Mbappe"}}]},
        ],
    }
    scorers = ESPNGamesSource._parse_soccer_scorers(soccer_summary, per_game=2)
    assert len(scorers) == 2, scorers
    assert scorers[0]["name"] == "R.Lewandowski", scorers
    assert scorers[0]["category"] == "GOAL" and scorers[0]["team"] == "BAR"
    assert scorers[1]["name"] == "K.Mbappe" and scorers[1]["team"] == "RMA"
    print("PASS  soccer keyEvents yield GOAL scorers for notable performers")

    # Weather schema default matches code Title Case.
    schema = json.load(open(os.path.join(
        os.path.dirname(__file__), "config_schema.json")))
    assert schema["properties"]["weather"]["properties"]["label"]["default"] == "Bayonne"
    print("PASS  weather label schema default is Title Case Bayonne")

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
