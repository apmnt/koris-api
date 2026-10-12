import json
from pathlib import Path
from typing import cast

import pytest

from koris_api import (
    Component,
    DataIssue,
    Season,
    SeasonData,
    api,
    get_season,
    load_season,
    save_season,
)
from koris_api.converters import from_genius_livestats
from koris_api.pipeline.types import BatchReport, GameOutcome, GameRef, Provider


@pytest.fixture
def dataset() -> SeasonData:
    raw = cast(
        dict[str, object],
        json.loads(
            (
                Path(__file__).parent / "fixtures/genius_sports/livestats_2514938.json"
            ).read_text()
        ),
    )
    game = from_genius_livestats(raw, match_id="2514938")
    return SeasonData(
        Season("4", "huki2526", "2025-2026", "Korisliiga"),
        (game,),
        (),
        1,
        tuple(Component),
        "2026-10-12T00:00:00+00:00",
    )


def test_public_season_fetch_uses_shared_pipeline_and_copies_games(
    monkeypatch: pytest.MonkeyPatch, dataset: SeasonData, tmp_path: Path
) -> None:
    outcome = GameOutcome(
        GameRef(Provider.GENIUS, "2514938"), tuple(Component), (), dataset.games[0]
    )
    calls: list[dict[str, object]] = []

    def fetch(
        season_id: str,
        category_id: str,
        components: tuple[Component, ...],
        **kwargs: object,
    ) -> BatchReport:
        calls.append(
            {
                "season_id": season_id,
                "category_id": category_id,
                "components": components,
                **kwargs,
            }
        )
        return BatchReport((outcome,))

    monkeypatch.setattr(api, "fetch_canonical_season", fetch)
    result = get_season(
        dataset.season, components=tuple(Component), cache_dir=tmp_path, limit_games=1
    )
    assert result.complete
    assert result.games[0].season == "2025-2026"
    assert calls[0]["cache_dir"] == tmp_path
    assert calls[0]["limit_games"] == 1
    assert result.limit_games == 1
    result.games[0].home.score = 99
    assert dataset.games[0].home.score == 80


def test_partial_season_retains_game_and_messages(
    monkeypatch: pytest.MonkeyPatch, dataset: SeasonData
) -> None:
    outcome = GameOutcome(
        GameRef(Provider.GENIUS, "2514938"),
        tuple(Component),
        (),
        dataset.games[0],
        issues=("home: player points do not sum to reported total",),
    )
    monkeypatch.setattr(
        api, "fetch_canonical_season", lambda *args, **kwargs: BatchReport((outcome,))
    )
    result = get_season(dataset.season)
    assert not result.complete and len(result.games) == 1
    assert result.issues[0].game_id == dataset.games[0].id
    assert "player points" in result.issues[0].messages[0]


def test_discovery_deduplicates_seasons_and_preserves_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "category": {
            "category_name": "Korisliiga",
            "seasons": [
                {"season_name": "2025-2026", "competition_id": "huki2526"},
                {"season_name": "2025-2026", "competition_id": "duplicate"},
                {"season_name": "2010-2011", "competition_id": "2010-2011"},
            ],
        }
    }
    monkeypatch.setattr(api.BasketFiAPI, "get_category", lambda *args: source)
    monkeypatch.setattr(api, "available_season_rows", lambda rows, category: rows)
    result = api.list_seasons(competition_id="huki2526")
    assert [season.id for season in result] == ["huki2526", "2010-2011"]
    assert result[1].historical and not result[0].historical


def test_snapshot_roundtrip_preserves_presence_and_failures(
    dataset: SeasonData, tmp_path: Path
) -> None:
    partial = SeasonData(
        dataset.season,
        dataset.games,
        (DataIssue(dataset.games[0].id, "genius", ("upstream failure",)),),
        2,
        dataset.components,
        dataset.fetched_at,
    )
    path = tmp_path / "snapshot.json"
    save_season(path, partial)
    restored = load_season(path)
    assert restored == partial
    assert not restored.complete
    raw = json.loads(path.read_text())
    raw["format"] = "unknown"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="format"):
        load_season(path)


@pytest.mark.parametrize("count", [-1, True, "1"])
def test_snapshot_rejects_invalid_counts(
    dataset: SeasonData, tmp_path: Path, count: object
) -> None:
    path = tmp_path / "snapshot.json"
    save_season(path, dataset)
    raw = json.loads(path.read_text())
    raw["requested_games"] = count
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="requested_games"):
        load_season(path)


@pytest.mark.parametrize("limit_games,max_workers", [(-1, 5), (None, 0)])
def test_fetch_rejects_invalid_configuration_before_io(
    limit_games: int | None, max_workers: int
) -> None:
    with pytest.raises(ValueError):
        get_season(
            Season("4", "huki2526", "2025-2026", "Korisliiga"),
            limit_games=limit_games,
            max_workers=max_workers,
        )
