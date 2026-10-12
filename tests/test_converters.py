import json
from copy import deepcopy
from pathlib import Path

import pytest
from google.protobuf.json_format import MessageToJson, Parse

from koris_api.converters import (
    ConversionError,
    from_basketfi,
    from_baskethotel,
    from_genius,
    from_genius_livestats,
    from_sportradar,
    validate_game,
)
from koris_api.schema.koris.v1 import game_pb2 as pb

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_basketfi_metadata_and_presence():
    raw = {
        "match_id": "001",
        "status": "Fixture",
        "fs_A": "0",
        "fs_B": "",
        "date": "2026-10-12",
        "time": "18:30:00",
    }
    game = from_basketfi(raw)
    assert game.id == "basketfi:match:001"
    assert game.time == "18:30"
    assert game.home.HasField("score") and game.home.score == 0
    assert not game.away.HasField("score")
    assert not game.home.HasField("win")
    assert not game.home.HasField("totals")


def test_sportradar_full_game_identity_links_and_roundtrips():
    box, events = load("sportradar/boxscore.json"), load("sportradar/playbyplay.json")
    original = deepcopy((box, events))
    game = from_sportradar(boxscore=box, playbyplay=events)
    assert game.status == pb.GAME_STATUS_FINAL
    assert game.home.score == 102 and game.away.score == 83
    assert game.home.win is True and game.away.win is False
    assert game.away.totals.rebounds == 30
    assert sum(p.stats.rebounds for p in game.away.players) == 28
    strong = next(p for p in game.home.players if p.name == "Stevie Strong")
    assert strong.stats.seconds_played == 1915
    assert strong.stats.HasField("assists") and strong.stats.assists == 7
    assert all(
        shot.event_id in {e.id for e in game.events}
        for p in game.home.players
        for shot in p.shots
    )
    miss = game.events[2]
    assert miss.HasField("made") and miss.made is False
    assert miss.game_clock == "09:50"
    assert miss.players[0].player_id == strong.id
    assert not game.home.totals.HasField("played")
    assert validate_game(game) == []
    assert pb.Game.FromString(game.SerializeToString()) == game
    assert Parse(MessageToJson(game), pb.Game()) == game
    assert (box, events) == original
    assert from_sportradar(boxscore=box, playbyplay=events) == game


def test_sportradar_unknowns_zeros_and_no_roster_events():
    events = load("sportradar/playbyplay.json")
    raw = events["periodData"][0]["events"][2]
    raw["success"], raw["x"], raw["y"] = None, 0, None
    events["periodData"] = [events["periodData"][0]]
    events["periodData"][0]["events"] = [raw]
    game = from_sportradar(playbyplay={"data": events})
    p = game.home.players[0]
    assert not p.HasField("stats")
    assert not p.shots[0].HasField("made")
    assert p.shots[0].HasField("x") and p.shots[0].x == 0
    assert not p.shots[0].HasField("y")
    assert validate_game(game) == []


def test_sportradar_rejects_combining_different_games():
    box, events = load("sportradar/boxscore.json"), load("sportradar/playbyplay.json")
    events["fixture"]["fixtureId"] = "different"
    with pytest.raises(ConversionError, match="different fixtures"):
        from_sportradar(boxscore=box, playbyplay=events)


def test_genius_before_normalization_preserves_minutes_and_totals():
    raw = load("genius_sports/boxscore.json")
    raw["teams"][0]["players"][0]["Shirt Number"] = "00"
    raw["teams"][0]["players"][0].pop("Steals")
    game = from_genius(match_id="2514938", boxscore=raw)
    p = game.home.players[0]
    assert p.jersey_number == "00"
    assert p.stats.seconds_played == pytest.approx(21.431667 * 60)
    assert not p.stats.HasField("steals")
    assert not p.stats.HasField("played")
    assert game.home.totals.points == 74
    assert game.home.win is False
    assert game.date == "2025-10-03"


def test_genius_livestats_overtime_and_absent_team_totals():
    data = {
        "tm": {
            "1": {
                "name": "Home",
                "pl": {
                    "1": {
                        "firstName": "Example",
                        "familyName": "Player",
                        "shirtNumber": "00",
                        "sPoints": 0,
                    }
                },
            },
            "2": {"name": "Away"},
        },
        "pbp": [
            {
                "actionNumber": 12,
                "actionType": "3pt",
                "period": 1,
                "periodType": "OVERTIME",
                "tno": 1,
                "gt": "04:05:50",
                "success": 0,
                "player": "Example Player",
                "shirtNumber": "00",
                "s1": 0,
                "s2": 0,
            }
        ],
    }
    game = from_genius_livestats(data, match_id="123", regulation_periods=4)
    assert game.events[0].period == 5
    assert game.events[0].game_clock == "04:05.5"
    assert game.home.players[0].shots[0].made is False
    assert len(game.home.players) == 1
    assert not game.home.totals.HasField("points")
    assert game.status == pb.GAME_STATUS_UNSPECIFIED
    with pytest.raises(ConversionError, match="regulation_periods"):
        from_genius_livestats(data, match_id="123")


def test_baskethotel_missing_values_and_scoped_game_id():
    box = {
        "teams": [
            {
                "team_name": "Home",
                "players": [
                    {
                        "Player": "#00 Example Player",
                        "PIST": "0",
                        "MIN": "02:05.5",
                        "2P": "0-1",
                    }
                ],
                "totals": {"PIST": "0"},
            }
        ]
    }
    events = {
        "teams": {"home": "Home", "away": "Away"},
        "events": [
            {
                "event_type": "shot_missed",
                "period": "P1",
                "team": "Home",
                "time": "02:05",
                "action": "2 pisteen heitto epäonnistui",
                "players": [{"name": "Example Player", "number": "00"}],
            }
        ],
    }
    game = from_baskethotel(
        game_id="400",
        league_id="2",
        season="2015-2016",
        boxscore=box,
        playbyplay=events,
    )
    p = game.home.players[0]
    assert game.external_ids[0].scope == "league:2:season:2015-2016"
    assert p.jersey_number == "00" and p.stats.seconds_played == 125.5
    assert not p.stats.HasField("steals")
    assert not game.home.totals.HasField("rebounds")
    assert game.events[0].made is False
    assert not game.events[0].HasField("game_clock")
    assert len(game.home.players) == 1
    assert validate_game(game) == []


def test_semantic_validation_reports_inconsistency_without_rewriting():
    game = from_basketfi({"match_id": "1", "fs_A": "0"})
    game.home.totals.points = 2
    issues = validate_game(game)
    assert issues == ["home: score and reported total points disagree"]
    assert game.home.score == 0 and game.home.totals.points == 2


def test_historical_html_events_have_resolved_players():
    genius = from_genius(
        match_id="2701971",
        playbyplay=(FIXTURES / "genius_sports/playbyplay_2701971.html").read_text(),
        regulation_periods=4,
    )
    assert len(genius.events) == 564
    assert len(genius.home.players) == 10
    assert validate_game(genius) == []
    html = (FIXTURES / "baskethotel/playbyplay.html").read_text()
    hotel = from_baskethotel(
        game_id="5883851",
        league_id="2",
        season="2015-2016",
        header=html,
        playbyplay=html,
        regulation_periods=4,
    )
    assert hotel.home.name == "Tapiolan Honka"
    assert hotel.away.name == "Jyväskylä Basketball Academy"
    assert len(hotel.events) == 571
    assert all(e.HasField("home") for e in hotel.events)
    assert hotel.home.players and hotel.away.players
    assert validate_game(hotel) == []


def test_baskethotel_raw_boxscore_preserves_all_reported_totals():
    html = """<table>
    <tr><th>Home</th><th>MIN</th><th>PIST</th><th>2P</th><th>LEV</th><th>S</th><th>R</th></tr>
    <tr><td>#00 Example</td><td>02:00</td><td>2</td><td>1-2</td><td>0</td><td>0</td><td></td></tr>
    <tr><td>Yhteensä</td><td></td><td>2</td><td>1-2</td><td>1</td><td>0</td><td></td></tr>
    </table>"""
    game = from_baskethotel(
        game_id="1",
        league_id="2",
        season="2015-2016",
        header={"teams": {"home": {"name": "Home"}}},
        boxscore=html,
    )
    assert game.home.totals.points == 2
    assert game.home.totals.two_pointers_attempted == 2
    assert game.home.totals.rebounds == 1
    assert not game.home.totals.HasField("steals")
    assert game.home.players[0].jersey_number == "00"


@pytest.mark.parametrize("value", [-1, "oops", 1.2, float("nan")])
def test_invalid_count_fails_instead_of_becoming_zero(value: object) -> None:
    with pytest.raises(ConversionError):
        from_basketfi({"match_id": "1", "fs_A": value})


def test_explicit_dnp_and_missing_time_are_different():
    box = {
        "match_info": {"home_team": "Home"},
        "teams": [
            {
                "team_name": "Home",
                "players": [
                    {"Player": "Absent", "Minutes": "DNP"},
                    {"Player": "Unknown"},
                ],
            }
        ],
    }
    game = from_genius(match_id="1", boxscore=box)
    assert game.home.players[0].stats.HasField("played")
    assert game.home.players[0].stats.played is False
    assert not game.home.players[0].stats.HasField("seconds_played")
    assert not game.home.players[1].stats.HasField("played")
