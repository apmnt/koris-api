"""Season exports and resumability using real payload fixtures and fake I/O."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from google.protobuf.json_format import Parse

from koris_api.pipeline import Component, acquire_game
from koris_api.pipeline.types import GameOutcome, GameRef, JSONObject
from koris_api.schema.koris.v1.game_pb2 import Game
from koris_api.services import canonical_games, game_exports, season_boxscores
from tests.test_pipeline import FIXTURE_ID, FixtureTransport, fixture


def match_row(
    match_id: str = "1", date: str = "2026-09-29", external_id: str | None = FIXTURE_ID
) -> JSONObject:
    return {
        "match_id": match_id,
        "date": date,
        "status": "Played",
        "match_external_id": external_id,
        "fs_A": "102",
        "fs_B": "83",
        "team_A_name": "Kataja Basket",
        "team_B_name": "Helsinki Seagulls",
    }


def fetch_fixture_game(
    reference: GameRef, components: tuple[Component, ...], **kwargs: object
) -> GameOutcome:
    return acquire_game(
        reference,
        components,
        transport=FixtureTransport(
            {"statistics": fixture("boxscore.json"), "pbp": fixture("playbyplay.json")}
        ),
    )


@pytest.mark.parametrize("binary", [False, True])
def test_canonical_season_limits_latest_played_game_and_roundtrips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binary: bool
) -> None:
    rows = [
        match_row("old", "2026-09-28", None),
        match_row("new"),
        {"match_id": "fixture", "date": "2026-10-01", "status": "Fixture"},
    ]
    original = deepcopy(rows)
    monkeypatch.setattr(
        canonical_games.BasketFiAPI, "get_matches", lambda **kwargs: {"matches": rows}
    )
    monkeypatch.setattr(canonical_games, "fetch_game", fetch_fixture_game)
    report = canonical_games.download_canonical_season(
        "huki2627", "4", tmp_path, tuple(Component), binary=binary, limit_games=1
    )
    assert report.failed == 0 and len(report.outcomes) == 1
    suffix = ".pb" if binary else ".pb.json"
    path = tmp_path / f"game_{FIXTURE_ID}{suffix}"
    game = (
        Game.FromString(path.read_bytes())
        if binary
        else Parse(path.read_text(), Game())
    )
    assert game.home.score == 102 and game.away.score == 83 and game.events
    saved_report = json.loads((tmp_path / "report.json").read_text())
    assert saved_report["requested_games"] == 1 and saved_report["failures"] == []
    assert rows == original


def test_canonical_season_reports_unresolved_source_without_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        canonical_games.BasketFiAPI,
        "get_matches",
        lambda **kwargs: {"matches": [match_row(external_id=None)]},
    )

    def unexpected_fetch(*args: object, **kwargs: object) -> None:
        pytest.fail("An unresolved source must not make a network request")

    monkeypatch.setattr(canonical_games, "fetch_game", unexpected_fetch)
    report = canonical_games.download_canonical_season(
        "huki2627", "4", tmp_path, (Component.BOXSCORE,)
    )
    assert report.failed == 1 and report.outcomes[0].game is None
    assert json.loads((tmp_path / "report.json").read_text())["failed_games"] == 1
    assert not list(tmp_path.glob("*.pb.json"))


def test_season_resume_fetches_only_missing_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {"matches": [match_row()]}
    before = deepcopy(raw)
    monkeypatch.setattr(
        season_boxscores.BasketFiAPI, "get_matches", lambda **kwargs: raw
    )
    calls: list[tuple[Component, ...]] = []

    def fetch(
        reference: GameRef, components: tuple[Component, ...], **kwargs: object
    ) -> GameOutcome:
        calls.append(components)
        return fetch_fixture_game(reference, components)

    monkeypatch.setattr(game_exports, "fetch_game", fetch)
    output = tmp_path / "season.json"
    season_boxscores.download_matches_with_boxscores(
        "huki2627", "4", str(output), include_advanced=True, verbose=False
    )
    first = json.loads(output.read_text())["matches"][0]["boxscore"]
    season_boxscores.download_matches_with_boxscores(
        "huki2627",
        "4",
        str(output),
        include_advanced=True,
        include_playbyplay=True,
        include_shot_chart=True,
        verbose=False,
    )
    season_boxscores.download_matches_with_boxscores(
        "huki2627",
        "4",
        str(output),
        include_advanced=True,
        include_playbyplay=True,
        include_shot_chart=True,
        verbose=False,
    )
    assert calls == [(Component.BOXSCORE,), (Component.PLAYBYPLAY, Component.SHOTS)]
    result = json.loads(output.read_text())
    assert result["matches"][0]["boxscore"] == first
    assert result["metadata"]["matches_with_playbyplay"] == 1
    assert result["metadata"]["matches_with_shot_chart"] == 1
    assert result["metadata"]["conversion_failures"] == 0
    assert raw == before


@pytest.mark.parametrize(
    "contents",
    [
        "not json",
        "null",
        '{"matches":false}',
        '{"matches":[null,{}, {"match_id":"1","boxscore":{}}]}',
    ],
)
def test_resume_ignores_corrupt_or_unusable_rows(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "resume.json"
    path.write_text(contents)
    loaded = game_exports.load_matches(path)
    assert set(loaded) <= {"1"}
    assert path.read_text() == contents


def test_enrich_records_unsupported_source_and_keeps_input_immutable() -> None:
    rows = [match_row(external_id=None)]
    before = deepcopy(rows)
    enriched, report = game_exports.enrich_matches(
        rows, (Component.BOXSCORE,), game_exports.external_reference
    )
    assert report.failed == 1
    assert enriched[0]["advanced_boxscore_error"]["error_type"] == "unsupported"
    assert rows == before


def test_batch_propagates_programming_errors_and_rejects_invalid_worker_count() -> None:
    from koris_api.pipeline import run_batch

    def bad_work(value: int) -> int:
        raise RuntimeError("programming error")

    with pytest.raises(RuntimeError, match="programming error"):
        run_batch([1], bad_work)
    with pytest.raises(ValueError, match="positive"):
        run_batch([1], lambda value: value, max_workers=0)
    assert run_batch([], lambda value: value) == ()


@pytest.mark.parametrize("failed", [False, True])
def test_league_script_backend_counts_partial_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    from koris_api.services import league_boxscores

    category = {
        "category": {
            "category_name": "Test league",
            "seasons": [{"season_name": "2026-2027", "competition_id": "huki2627"}],
        }
    }
    monkeypatch.setattr(
        league_boxscores.BasketFiAPI, "get_category", lambda *args: category
    )
    monkeypatch.setattr(
        league_boxscores,
        "available_season_rows",
        lambda seasons, category_id: seasons,
    )

    def download(**kwargs: object) -> None:
        Path(str(kwargs["output_file"])).write_text(
            json.dumps(
                {"metadata": {"conversion_failures": int(failed)}, "matches": []}
            )
        )

    monkeypatch.setattr(league_boxscores, "download_season_comprehensive", download)
    result = league_boxscores.download_league_boxscores_all_seasons(
        "2", str(tmp_path), start_year=2026, limit_seasons=1, verbose=False
    )
    assert result == int(failed)


def test_league_backend_does_not_silently_succeed_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from koris_api.services import league_boxscores

    monkeypatch.setattr(
        league_boxscores.BasketFiAPI,
        "get_category",
        lambda *args: {
            "category": {
                "category_name": "Test",
                "seasons": [{"season_name": "2026-2027", "competition_id": "huki2627"}],
            }
        },
    )
    monkeypatch.setattr(
        league_boxscores,
        "available_season_rows",
        lambda seasons, category_id: seasons,
    )
    monkeypatch.setattr(
        league_boxscores, "download_season_comprehensive", lambda **kwargs: None
    )
    with pytest.raises(RuntimeError, match="invalid output"):
        league_boxscores.download_league_boxscores_all_seasons(
            "2", str(tmp_path), start_year=2026, verbose=False
        )
