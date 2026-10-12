"""Exercise all provider adapters through the shared acquisition boundary."""

import json
from pathlib import Path
from typing import TypedDict
from urllib.parse import parse_qs, urlsplit

import pytest

from koris_api.data import fields, records
from koris_api.pipeline import Component, Provider, acquire_game, resolve_reference
from koris_api.pipeline.games import export_legacy
from koris_api.pipeline.types import FetchOutcome, Request
from tests.test_pipeline import FixtureTransport

FIXTURES = Path(__file__).parent / "fixtures"


def test_livestats_fetches_once_for_all_components_and_preserves_source_facts() -> None:
    raw = json.loads((FIXTURES / "genius_sports/livestats_2514938.json").read_text())
    transport = FixtureTransport({"livestats": raw})
    outcome = acquire_game(
        resolve_reference("2514938"), tuple(Component), transport=transport
    )
    assert outcome.complete, (outcome.conversion_failure, outcome.issues)
    assert len(transport.calls) == 1
    game = outcome.game
    assert game is not None
    assert (game.home.score, game.away.score) == (80, 78)
    assert len(game.events) == len(raw["pbp"]) == 588
    assert (
        sum(len(p.shots) for team in (game.home, game.away) for p in team.players)
        == 171
    )
    for side, team in (("1", game.home), ("2", game.away)):
        assert team.totals.points == raw["tm"][side]["tot_sPoints"]
        assert sum(p.stats.points for p in team.players) == team.score
    exported = export_legacy(outcome, {})
    assert len(records(fields(exported["playbyplay"])["events"])) == 588
    assert len(records(fields(exported["shot_chart"])["shots"])) == sum(
        len(raw["tm"][side]["shot"]) for side in ("1", "2")
    )
    assert (
        fields(records(fields(exported["boxscore"])["teams"])[0]["totals"])["Points"]
        == 80
    )


class HotelTransport:
    def __init__(self) -> None:
        self.calls: list[Request] = []

    def fetch(self, request: Request) -> FetchOutcome:
        self.calls.append(request)
        part = parse_qs(urlsplit(request.url).query).get("request[0][part]", [""])[0]
        if request.name == "header":
            return FetchOutcome(
                request,
                (FIXTURES / "baskethotel/playbyplay.html").read_text()
                + "<script>state: 'header-state'</script>",
            )
        if part == "play-by-play":
            return FetchOutcome(
                request,
                "<script>state: 'pbp-state'</script><li data-filter='quarter' data-filter-selected='true' data-filter-value='1'></li><li data-filter='quarter' data-filter-selected='true' data-filter-value='2'></li>",
            )
        return FetchOutcome(
            request, (FIXTURES / "baskethotel/playbyplay.html").read_text()
        )


def test_hotel_stateful_playbyplay_expands_selected_filters() -> None:
    transport = HotelTransport()
    reference = resolve_reference(
        "5883851", provider=Provider.BASKETHOTEL, league_id="2", season_id="121333"
    )
    outcome = acquire_game(reference, (Component.PLAYBYPLAY,), transport=transport)
    assert outcome.complete, (outcome.conversion_failure, outcome.issues)
    assert len(transport.calls) == 3
    query = parse_qs(urlsplit(transport.calls[-1].url).query)
    assert query["request[0][state]"] == ["pbp-state"]
    assert query["request[0][part]"] == ["play-by-play-actions"]
    assert query["request[0][param][filter][quarter]"] == ["1,2"]
    assert outcome.game is not None and len(outcome.game.events) == 571
    assert fields(export_legacy(outcome, {})["playbyplay"])["events"]


def test_hotel_unsupported_shots_preserve_playbyplay() -> None:
    outcome = acquire_game(
        resolve_reference(
            "5883851", provider=Provider.BASKETHOTEL, league_id="2", season_id="121333"
        ),
        (Component.PLAYBYPLAY, Component.SHOTS),
        transport=HotelTransport(),
    )
    assert not outcome.complete and outcome.game is not None and outcome.game.events
    assert any(f.failure and f.failure.kind == "unsupported" for f in outcome.fetches)
    exported = export_legacy(outcome, {})
    assert (
        exported["playbyplay"]
        and fields(exported["shot_chart_error"])["error_type"] == "unsupported"
    )


def test_genius_html_uses_explicit_competition_and_retains_event_players() -> None:
    transport = FixtureTransport(
        {"playbyplay": (FIXTURES / "genius_sports/playbyplay_2701971.html").read_text()}
    )
    reference = resolve_reference(
        "2701971", provider=Provider.GENIUS_HTML, competition_id="42145"
    )
    outcome = acquire_game(reference, (Component.PLAYBYPLAY,), transport=transport)
    assert outcome.complete
    assert transport.calls[0].url.endswith(
        "/competition/42145/match/2701971/playbyplay"
    )
    assert outcome.game is not None and len(outcome.game.events) == 564
    assert len(outcome.game.home.players) == 10


class ReferenceArgs(TypedDict, total=False):
    source_id: str
    provider: Provider
    regulation_periods: int
    clock_mode: str


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_id": " "},
        {"source_id": "1", "regulation_periods": 0},
        {"source_id": "1", "clock_mode": "elapsed"},
        {"source_id": "1", "provider": Provider.SPORTRADAR},
    ],
)
def test_invalid_source_context_is_rejected_before_network(
    kwargs: ReferenceArgs,
) -> None:
    with pytest.raises(ValueError):
        resolve_reference(**kwargs)


def test_incomplete_archive_roster_is_reported_without_changing_source_stats() -> None:
    from copy import deepcopy

    raw = json.loads((FIXTURES / "sportradar/incomplete_roster.json").read_text())
    before = deepcopy(raw)
    source_id = raw["fixture"]["fixtureId"]
    outcome = acquire_game(
        resolve_reference(source_id),
        (Component.BOXSCORE,),
        transport=FixtureTransport({"statistics": raw}),
    )
    assert not outcome.complete and outcome.game is not None
    assert outcome.issues == ("home: player points do not sum to reported total",)
    assert outcome.game.home.totals.points == outcome.game.home.score == 76
    assert sum(p.stats.points for p in outcome.game.home.players) == 60
    exported = export_legacy(outcome, {})
    assert (
        fields(records(fields(exported["boxscore"])["teams"])[0]["totals"])["Points"]
        == 76
    )
    assert "player points" in str(exported["conversion_error"])
    assert raw == before
