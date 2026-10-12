"""Opt-in live contracts: connectivity, source facts, canonical conversion, CLI exports."""

import json
import os
import sys
from pathlib import Path

import pytest

from koris_api import cli
from koris_api.basketfi_api import BasketFiAPI
from koris_api.basketfi_parser import BasketFiParser
from koris_api.converters import validate_game
from koris_api.data import fields, records
from koris_api.genius_api import GeniusSportsAPI
from koris_api.pipeline import Component, Provider, fetch_game, resolve_reference
from koris_api.pipeline.transport import validate_json
from koris_api.pipeline.types import GameOutcome, JSONObject
from koris_api.schema.koris.v1 import game_pb2 as pb
from koris_api.seasons import current_season_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 to run live checks",
    ),
]


@pytest.fixture(scope="module")
def live_season() -> str:
    return os.getenv("KORIS_LIVE_SEASON", current_season_id())


@pytest.fixture(scope="module")
def live_matches(live_season: str) -> dict[str, object]:
    return BasketFiAPI.get_matches(live_season, "4")


@pytest.fixture(scope="module")
def live_played_match(live_matches: dict[str, object]) -> JSONObject:
    played = [
        row
        for row in BasketFiParser.extract_matches(live_matches)
        if BasketFiParser.is_match_played(row)
        and row.get("match_external_id")
        and row.get("date")
    ]
    assert played, (
        "Live season has no played games with external data; set KORIS_LIVE_SEASON explicitly"
    )
    result = validate_json(max(played, key=lambda row: str(row["date"])))
    assert isinstance(result, dict)
    return result


@pytest.fixture(scope="module", params=["current", "genius", "baskethotel"])
def live_game(
    request: pytest.FixtureRequest, live_played_match: JSONObject
) -> GameOutcome:
    if request.param == "current":
        outcome = fetch_game(
            resolve_reference(str(live_played_match["match_external_id"])),
            tuple(Component),
            basketfi=live_played_match,
        )
    elif request.param == "genius":
        outcome = fetch_game(resolve_reference("2514938"), tuple(Component))
    else:
        outcome = fetch_game(
            resolve_reference(
                "239490", provider=Provider.BASKETHOTEL, league_id="4", season_id="8589"
            ),
            (Component.BOXSCORE, Component.PLAYBYPLAY),
        )
    failures = [f.failure.message for f in outcome.fetches if f.failure]
    assert outcome.complete, (
        outcome.reference.key,
        failures,
        outcome.conversion_failure,
        outcome.issues,
    )
    return outcome


def test_live_metadata(
    live_matches: dict[str, object],
    live_played_match: JSONObject,
    live_season: str,
) -> None:
    matches = BasketFiParser.extract_matches(live_matches)
    assert matches
    assert BasketFiParser.extract_teams_from_matches(
        BasketFiParser.parse_matches(matches)
    )
    detail = fields(BasketFiAPI.get_match(str(live_played_match["match_id"]))["match"])
    assert str(detail["match_id"]) == str(live_played_match["match_id"])
    assert detail["club_A_name"] and detail["club_B_name"]
    assert detail["fs_A"] == live_played_match["fs_A"]
    assert detail["fs_B"] == live_played_match["fs_B"]
    # Lineups are optional in metadata; verify them when the source provides them.
    if detail.get("lineups"):
        assert isinstance(detail["lineups"], (list, dict))
    category = fields(BasketFiAPI.get_category(live_season, "4")["category"])
    assert str(category["category_id"]) == "4" and records(category["seasons"])
    team = fields(
        BasketFiAPI.get_team(str(live_played_match["team_A_id"]), live_season, "4")[
            "team"
        ]
    )
    assert str(team["team_id"]) == str(live_played_match["team_A_id"])
    assert records(team["players"])


def test_live_canonical_game_matches_source_facts(live_game: GameOutcome) -> None:
    game = live_game.game
    assert game is not None
    assert game.home.name and game.away.name and game.home.name != game.away.name
    assert game.home.players and game.away.players
    assert validate_game(game) == []
    assert pb.Game.FromString(game.SerializeToString()) == game
    if Component.BOXSCORE in live_game.requested:
        assert game.home.HasField("score") and game.away.HasField("score")
        assert game.home.totals.points == game.home.score
        assert game.away.totals.points == game.away.score
        for team in (game.home, game.away):
            assert sum(p.stats.points for p in team.players) == team.score
    if Component.PLAYBYPLAY in live_game.requested:
        assert game.events
        scored = [
            e
            for e in game.events
            if e.HasField("home_score") and e.HasField("away_score")
        ]
        assert scored
        if game.home.HasField("score"):
            assert (scored[-1].home_score, scored[-1].away_score) == (
                game.home.score,
                game.away.score,
            )
    if Component.SHOTS in live_game.requested:
        shots = [
            s for team in (game.home, game.away) for p in team.players for s in p.shots
        ]
        assert shots and any(s.HasField("x") and s.HasField("y") for s in shots)
        assert all(s.HasField("game_clock") for s in shots if s.HasField("event_id"))


def test_live_legacy_genius_adapter() -> None:
    box = GeniusSportsAPI.get_match_boxscore("2514938")
    pbp = GeniusSportsAPI.get_match_playbyplay("2514938")
    assert len(box["teams"]) == 2
    assert all(t["players"] for t in box["teams"])
    assert pbp["events"]
    scores = box["match_info"]
    assert (
        pbp["events"][-1]["score"] == f"{scores['home_score']}-{scores['away_score']}"
    )


@pytest.mark.parametrize("output_format", ["legacy", "protobuf-json", "protobuf"])
def test_live_current_season_cli_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_season: str,
    output_format: str,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "koris-api",
            "season",
            "4",
            live_season,
            "--box-score",
            "--playbyplay",
            "--shot-chart",
            "--limit-games",
            "1",
            "--format",
            output_format,
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert cli.main() == 0
    if output_format == "legacy":
        data = json.loads(
            (tmp_path / f"season_boxscores_{live_season}.json").read_text()
        )
        assert data["metadata"]["played_matches_saved"] == 1
        assert data["metadata"]["conversion_failures"] == 0
        assert data["matches"][0]["boxscore"]["teams"]
        assert data["matches"][0]["playbyplay"]["events"]
        assert data["matches"][0]["shot_chart"]["shots"]
    else:
        from google.protobuf.json_format import Parse

        directory = tmp_path / live_season
        report = json.loads((directory / "report.json").read_text())
        assert report["requested_games"] == 1 and report["failed_games"] == 0
        paths = list(
            directory.glob("*.pb" if output_format == "protobuf" else "*.pb.json")
        )
        assert len(paths) == 1
        game = pb.Game()
        if output_format == "protobuf":
            game.ParseFromString(paths[0].read_bytes())
        else:
            Parse(paths[0].read_text(), game)
        assert game.home.players and game.events
        assert validate_game(game) == []
