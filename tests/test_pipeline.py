"""Contracts for pure planning, owned I/O, and compatibility exports."""

import json
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
import requests

from koris_api.analysis.possessions import enrich_possessions
from koris_api.basketfi_parser import BasketFiParser
from koris_api.converters import (
    ConversionError,
    from_baskethotel,
    from_genius_livestats,
)
from koris_api.pipeline import (
    Component,
    Provider,
    acquire_game,
    missing_components,
    plan_requests,
    resolve_reference,
    run_batch,
)
from koris_api.pipeline.cache import CachedTransport
from koris_api.pipeline.games import export_legacy
from koris_api.pipeline.transport import RequestsTransport, RetryPolicy, validate_json
from koris_api.pipeline.types import FetchOutcome, JSONObject, Request
from koris_api.schema.koris.v1.game_pb2 import Game

FIXTURES = Path(__file__).parent / "fixtures" / "sportradar"
FIXTURE_ID = "44cfe520-a1e7-11f1-8d0e-4fe1f25b2942"


def fixture(name: str) -> JSONObject:
    value = validate_json(json.loads((FIXTURES / name).read_text()))
    assert isinstance(value, dict)
    return value


class FixtureTransport:
    def __init__(self, payloads: dict[str, JSONObject]) -> None:
        self.payloads = payloads
        self.calls: list[Request] = []

    def fetch(self, request: Request) -> FetchOutcome:
        self.calls.append(request)
        return FetchOutcome(request, self.payloads[request.name])


class HTTPClient:
    def __init__(self, statuses: list[int], payload: object) -> None:
        self.statuses = list(statuses)
        self.payload = payload
        self.calls: list[float] = []

    def get(
        self, url: str, *, timeout: float, headers: dict[str, str]
    ) -> requests.Response:
        self.calls.append(timeout)
        response = requests.Response()
        response.status_code = self.statuses.pop(0)
        response.url = url
        response._content = json.dumps(self.payload).encode()
        return response


def test_plans_share_payloads_and_require_real_context() -> None:
    components = tuple(Component)
    reference = resolve_reference(FIXTURE_ID)
    requests_ = plan_requests(reference, components)
    assert [r.name for r in requests_] == ["statistics", "pbp"]
    assert requests_[1].components == (Component.PLAYBYPLAY, Component.SHOTS)
    assert len(plan_requests(resolve_reference("123"), components)) == 1
    with pytest.raises(ValueError, match="competition"):
        resolve_reference("123", provider=Provider.GENIUS_HTML)
    with pytest.raises(ValueError, match="league and season"):
        resolve_reference("123", provider=Provider.BASKETHOTEL)


def test_acquisition_is_deterministic_and_leaves_sources_untouched() -> None:
    payloads = {
        "statistics": fixture("boxscore.json"),
        "pbp": fixture("playbyplay.json"),
    }
    original = deepcopy(payloads)
    transport = FixtureTransport(payloads)
    outcome = acquire_game(
        resolve_reference(FIXTURE_ID), tuple(Component), transport=transport
    )
    assert outcome.complete and outcome.game is not None
    assert len(transport.calls) == 2
    assert outcome.game.home.score == 102
    assert len(outcome.game.events) == 48
    assert payloads == original
    repeated = acquire_game(outcome.reference, tuple(Component), transport=transport)
    assert repeated.game == outcome.game


def test_partial_payload_failure_keeps_success_and_reports_failure() -> None:
    transport = FixtureTransport(
        {"statistics": fixture("boxscore.json"), "pbp": {"data": None}}
    )
    outcome = acquire_game(
        resolve_reference(FIXTURE_ID),
        (Component.BOXSCORE, Component.PLAYBYPLAY),
        transport=transport,
    )
    assert not outcome.complete and outcome.game is not None
    result = export_legacy(outcome, {"match_id": "1"})
    assert result["boxscore"] and "playbyplay_error" in result
    assert "playbyplay" not in result


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_http_failures_are_not_retried(status: int) -> None:
    client = HTTPClient([status], {})
    sleeps: list[float] = []
    outcome = RequestsTransport(client, RetryPolicy(), sleeps.append).fetch(
        Request("fixture", "https://example.test", ())
    )
    assert outcome.failure is not None
    assert outcome.failure.status_code == status and not outcome.failure.retryable
    assert client.calls == [30.0] and sleeps == []


def test_transient_retry_is_bounded_and_uses_timeouts() -> None:
    client = HTTPClient([429, 503, 200], {"ok": True})
    sleeps: list[float] = []
    outcome = RequestsTransport(
        client, RetryPolicy(retries=2, timeout=7), sleeps.append
    ).fetch(Request("fixture", "https://example.test", ()))
    assert outcome.failure is None and outcome.payload == {"ok": True}
    assert client.calls == [7, 7, 7] and sleeps == [0.6, 1.2]


def test_raw_checkpoint_is_independent_of_component_and_can_be_invalidated(
    tmp_path: Path,
) -> None:
    underlying = FixtureTransport({"pbp": fixture("playbyplay.json")})
    cached = CachedTransport(underlying, tmp_path)
    first = Request("pbp", "https://example.test/game/pbp", (Component.PLAYBYPLAY,))
    second = Request("pbp", first.url, (Component.SHOTS,))
    assert cached.fetch(first).payload == cached.fetch(second).payload
    assert len(underlying.calls) == 1
    cached.invalidate(first)
    _ = cached.fetch(second)
    assert len(underlying.calls) == 2


def test_resume_retries_missing_failures_and_can_skip_permanent_failures() -> None:
    cached: JSONObject = {
        "boxscore": {"teams": [{"team_name": "Home"}]},
        "playbyplay_error": {"message": "404"},
    }
    original = deepcopy(cached)
    assert missing_components(tuple(Component), cached) == (
        Component.PLAYBYPLAY,
        Component.SHOTS,
    )
    assert missing_components(tuple(Component), cached, retry_failures=False) == (
        Component.SHOTS,
    )
    assert cached == original


def test_batch_output_order_is_stable_when_completion_order_is_reversed() -> None:
    ready = Event()

    def work(value: int) -> int:
        if value == 0:
            assert ready.wait(timeout=2)
        else:
            ready.set()
        return value * 2

    assert run_batch([0, 1], work, max_workers=2) == (0, 2)


def test_metadata_and_possession_functions_do_not_mutate_inputs() -> None:
    source = {"matches": [{"status": "unknown", "fs_A": "0"}]}
    original = deepcopy(source)
    matches = BasketFiParser.extract_matches(source)
    assert matches[0]["status"] == "Played" and source == original
    events = [
        {
            "event_type": "2pt",
            "team": "1",
            "time": "09:00",
            "period": "P1",
            "action": "made",
        }
    ]
    before = deepcopy(events)
    enriched, _ = enrich_possessions(events)
    assert events == before
    assert enriched[0] is not events[0]


def livestats() -> JSONObject:
    return {
        "tm": {
            "1": {
                "name": "Home",
                "pl": {
                    "1": {"name": "Kandén", "shirtNumber": "00", "sMinutes": "3:60"}
                },
                "shot": [
                    {
                        "pno": 1,
                        "actionNumber": 0,
                        "x": 0,
                        "y": 20,
                        "r": 0,
                        "per": 1,
                        "actionType": "3pt",
                    }
                ],
            },
            "2": {"name": "Away"},
        },
        "pbp": [
            {
                "actionNumber": 0,
                "actionType": "3pt",
                "tno": 1,
                "pno": 1,
                "player": "Kanden",
                "shirtNumber": "00",
                "success": 0,
                "period": 1,
                "gt": "09:00",
            }
        ],
    }


def test_livestats_joins_shots_by_action_and_roster_slot_without_duplicates() -> None:
    raw = livestats()
    original = deepcopy(raw)
    game = from_genius_livestats(raw, match_id="1", regulation_periods=4)
    assert len(game.home.players) == 1
    player = game.home.players[0]
    assert player.name == "Kandén" and player.jersey_number == "00"
    assert player.stats.seconds_played == 240
    assert len(player.shots) == 1
    assert player.shots[0].event_id == "0" and player.shots[0].x == 0
    assert player.shots[0].made is False and raw == original


def test_livestats_does_not_guess_a_shot_player() -> None:
    raw = livestats()
    tm = raw["tm"]
    assert isinstance(tm, dict)
    home = tm["1"]
    assert isinstance(home, dict)
    home["shot"] = [{"pno": 999, "actionNumber": 0}]
    with pytest.raises(ConversionError, match="roster slot"):
        from_genius_livestats(raw, match_id="1", regulation_periods=4)


def test_baskethotel_keeps_opposing_foul_players_in_their_own_rosters() -> None:
    game = from_baskethotel(
        game_id="1",
        league_id="4",
        season="92143",
        header={"teams": {"home": {"name": "Full Home"}, "away": {"name": "Away"}}},
        boxscore={
            "teams": [
                {
                    "team_name": "Home",
                    "players": [{"Player": "#00Home Player", "MIN": "-"}],
                },
                {
                    "team_name": "Away",
                    "players": [{"Player": "#5 Away Player", "MIN": "01:00"}],
                },
            ]
        },
        playbyplay={
            "events": [
                {
                    "event_type": "foul",
                    "team": "Home",
                    "action_raw": "teki virheen, rikottu pelaaja:",
                    "players": [
                        {"name": "Home Player", "number": "00"},
                        {"name": "Away Player", "number": "5"},
                    ],
                }
            ]
        },
    )
    assert len(game.home.players) == len(game.away.players) == 1
    assert game.home.players[0].jersey_number == "00"
    assert not game.home.players[0].stats.HasField("seconds_played")
    assert not game.home.players[0].stats.HasField("played")
    event = game.events[0]
    assert event.players[0].player_id == game.home.players[0].id
    assert event.players[1].player_id == game.away.players[0].id
    assert event.players[1].role == "fouled"


def test_livestats_standalone_shot_is_not_duplicated() -> None:
    raw = livestats()
    raw["pbp"] = []
    tm = raw["tm"]
    assert isinstance(tm, dict)
    home = tm["1"]
    assert isinstance(home, dict)
    shots = home["shot"]
    assert isinstance(shots, list)
    home["shot"] = shots * 2
    game = from_genius_livestats(raw, match_id="1", regulation_periods=4)
    assert len(game.home.players[0].shots) == 1
    assert not game.home.players[0].shots[0].HasField("event_id")


def test_livestats_conflicting_shot_result_is_reported() -> None:
    raw = livestats()
    tm = raw["tm"]
    assert isinstance(tm, dict)
    home = tm["1"]
    assert isinstance(home, dict)
    home["shot"] = [{"pno": 1, "actionNumber": 0, "r": 1}]
    with pytest.raises(ConversionError, match="result disagrees"):
        from_genius_livestats(raw, match_id="1", regulation_periods=4)


def test_historical_source_identity_must_match_requested_game() -> None:
    with pytest.raises(ConversionError, match="different game"):
        from_baskethotel(
            game_id="1",
            league_id="4",
            season="92143",
            header={"game_info": {"game_id": "2"}},
        )


@pytest.mark.parametrize("output_format", ["legacy", "protobuf-json", "protobuf"])
def test_cli_keeps_partial_exports_and_exits_unsuccessfully(
    output_format: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from koris_api import cli
    from koris_api.pipeline.types import GameOutcome, GameRef

    def fetch_partial(
        reference: GameRef,
        components: tuple[Component, ...],
        **kwargs: object,
    ) -> GameOutcome:
        return acquire_game(
            reference,
            components,
            transport=FixtureTransport(
                {
                    "statistics": fixture("boxscore.json"),
                    "pbp": {"data": None},
                }
            ),
        )

    monkeypatch.setattr(cli, "fetch_game", fetch_partial)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "koris-api",
            "match",
            FIXTURE_ID,
            "--box-score",
            "--playbyplay",
            "--format",
            output_format,
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert cli.main() == 1
    if output_format == "legacy":
        assert (tmp_path / f"genius_match_{FIXTURE_ID}.json").exists()
        assert not (tmp_path / f"genius_playbyplay_{FIXTURE_ID}.json").exists()
    else:
        from google.protobuf.json_format import Parse

        game = Game()
        if output_format == "protobuf":
            game.ParseFromString((tmp_path / f"game_{FIXTURE_ID}.pb").read_bytes())
        else:
            Parse((tmp_path / f"game_{FIXTURE_ID}.pb.json").read_text(), game)
        assert game.home.players and game.away.players
        assert not game.events


def test_successful_retry_clears_old_component_error() -> None:
    outcome = acquire_game(
        resolve_reference(FIXTURE_ID),
        (Component.BOXSCORE,),
        transport=FixtureTransport({"statistics": fixture("boxscore.json")}),
    )
    assert outcome.complete
    original: JSONObject = {"advanced_boxscore_error": {"error_message": "404"}}
    exported = export_legacy(outcome, original)
    assert exported["boxscore"] and "advanced_boxscore_error" not in exported
    assert "advanced_boxscore_error" in original


def test_malformed_nested_records_fail_only_the_affected_component() -> None:
    box = fixture("boxscore.json")
    box["statistics"] = {
        "data": {
            "base": {
                "home": {"entity": {"points": 102}, "persons": "invalid"},
                "away": {"entity": {"points": 83}},
            }
        }
    }
    original = deepcopy(box)
    outcome = acquire_game(
        resolve_reference(FIXTURE_ID),
        (Component.BOXSCORE, Component.PLAYBYPLAY),
        transport=FixtureTransport(
            {"statistics": box, "pbp": fixture("playbyplay.json")}
        ),
    )
    assert not outcome.complete and outcome.game is not None
    assert outcome.fetches[0].failure is not None
    assert outcome.fetches[0].failure.kind == "invalid_payload"
    exported = export_legacy(outcome, {})
    assert "playbyplay" in exported and "boxscore" not in exported
    assert original == box
