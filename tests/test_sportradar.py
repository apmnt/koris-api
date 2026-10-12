"""Regression coverage using the official widget's 2026–27 game payloads."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from koris_api.boxscore_normalizer import normalize_boxscore
from koris_api.genius_api import GeniusSportsAPI
from koris_api.services.season_boxscores import download_matches_with_boxscores
from koris_api.sportradar_api import fetch_fixture
from koris_api.sportradar_parser import duration_clock, parse_boxscore, parse_playbyplay

FIXTURES = Path(__file__).parent / "fixtures" / "sportradar"
MATCH_ID = "44cfe520-a1e7-11f1-8d0e-4fe1f25b2942"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_boxscore_preserves_official_totals_and_player_identity():
    raw = fixture("boxscore.json")
    original = deepcopy(raw)
    box = normalize_boxscore(parse_boxscore(raw), source="genius")
    assert box["source"] == "sportradar"
    home, away = box["teams"]
    assert (home["totals"]["Points"], away["totals"]["Points"]) == (102, 83)
    # Two away rebounds belong to the team, not an individual player.
    assert away["totals"]["REB"] == 30
    rebounds: list[int] = []
    for p in away["players"]:
        value = p["REB"]
        assert isinstance(value, int)
        rebounds.append(value)
    assert sum(rebounds) == 28
    strong = next(p for p in home["players"] if p["player"] == "Stevie Strong")
    assert strong["Minutes"] == "31:55"
    assert strong["Points"] == 26
    assert strong["starter"] is True and strong["played"] is True
    assert strong["player_id"] == "00b8b2c2-a83a-11f1-ac95-dbfd521311a7"
    assert raw == original


def test_playbyplay_keeps_clocks_outcomes_order_and_source_ids():
    raw = fixture("playbyplay.json")
    original = deepcopy(raw)
    pbp = parse_playbyplay(raw)
    miss, made = pbp["events"][2], pbp["events"][4]
    assert miss["time"] == "09:50"
    assert miss["success"] is False
    assert isinstance(miss["action"], str) and miss["action"].startswith("missed ")
    assert made["time"] == "09:44" and made["score"] == "2-0"
    assert made["success"] is True
    assert isinstance(made["action"], str) and made["action"].startswith("made ")
    assert made["event_id"] == raw["periodData"][0]["events"][4]["eventId"]
    assert pbp["events"][-1]["score"] == "102-83"
    assert pbp["events"][-1]["period"] == "Q4"
    assert {p["player_id"] for p in pbp["players"]} >= {made["player_id"]}
    assert raw == original


@pytest.mark.parametrize(
    "value, expected",
    [
        ("PT10M0S", "10:00"),
        ("PT0M0S", "00:00"),
        ("PT9M5.25S", "09:05.25"),
        ("PT0.25S", "00:00.25"),
        ("PT200M", "200:00"),
        (None, None),
    ],
)
def test_duration_clock(value, expected):
    assert duration_clock(value) == expected


def test_overtime_and_unknown_shot_outcome():
    raw = fixture("playbyplay.json")
    period = raw["periodData"][0]
    period["isOvertime"] = True
    period["periodId"] = 5
    period["events"] = [period["events"][2]]
    period["events"][0]["success"] = None
    raw["periodData"] = [period]
    event = parse_playbyplay(raw)["events"][0]
    assert event["period"] == "OT1"
    assert isinstance(event["action"], str)
    assert not event["action"].startswith(("made ", "missed "))


def test_missing_data_is_a_failure():
    raw = fixture("playbyplay.json")
    with pytest.raises(ValueError, match="box score"):
        parse_boxscore(raw)
    raw["periodData"] = []
    with pytest.raises(ValueError, match="events"):
        parse_playbyplay(raw)


def test_public_api_routes_uuid_games_to_sportradar():
    with patch(
        "koris_api.genius_api.fetch_fixture", return_value=fixture("boxscore.json")
    ) as fetch:
        assert GeniusSportsAPI.get_match_boxscore(MATCH_ID)["source"] == "sportradar"
        assert fetch.call_args.args == (MATCH_ID, "statistics")
    with patch(
        "koris_api.genius_api.fetch_fixture", return_value=fixture("playbyplay.json")
    ) as fetch:
        assert GeniusSportsAPI.get_match_playbyplay(MATCH_ID)["events"]
        assert fetch.call_args.args == (MATCH_ID, "pbp")


def test_widget_fetch_uses_fixture_id_and_checks_http_failure():
    client = Mock()
    client.get.return_value.json.return_value = {"data": {"fixture": {}}}
    assert fetch_fixture(MATCH_ID, "pbp", session=client) == {"fixture": {}}
    assert client.get.call_args.kwargs["params"] == {
        "fixtureId": MATCH_ID,
        "sub": "pbp",
    }
    client.get.return_value.raise_for_status.assert_called_once()


def test_season_fetch_exports_both_components_with_explicit_provider(tmp_path):
    matches = {
        "matches": [
            {
                "match_id": "1005902",
                "match_external_id": MATCH_ID,
                "category_external_id": "12a43729-9bb6-11f1-9fef-97a68955b80f",
                "date": "2026-09-29",
                "time": "18:30:00",
                "status": "Played",
                "fs_A": "102",
                "fs_B": "83",
                "team_A_name": "Kataja Basket",
                "team_B_name": "Helsinki Seagulls",
            }
        ]
    }
    output = tmp_path / "season.json"
    with (
        patch(
            "koris_api.services.season_boxscores.BasketFiAPI.get_matches",
            return_value=matches,
        ),
        patch("requests.Session.get") as get,
    ):

        def response(url, **kwargs):
            payload = fixture(
                "boxscore.json" if "statistics" in url else "playbyplay.json"
            )
            return Mock(status_code=200, json=lambda: {"data": payload})

        get.side_effect = response
        download_matches_with_boxscores(
            "huki2627",
            "4",
            str(output),
            include_advanced=True,
            include_playbyplay=True,
            verbose=False,
        )
        assert get.call_count == 2
        assert all("sportradar.com" in call.args[0] for call in get.call_args_list)
    result = json.loads(output.read_text())
    assert result["metadata"]["source"] == "sportradar"
    assert result["metadata"]["matches_with_boxscore"] == 1
    assert result["metadata"]["matches_with_playbyplay"] == 1
    assert result["metadata"]["matches_failed"] == 0
    assert result["matches"][0]["playbyplay"]["events"][-1]["score"] == "102-83"
