"""Canonical season export using the shared game workflow."""

from pathlib import Path
from typing import cast

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..pipeline import (
    BatchReport,
    Component,
    Provider,
    fetch_game,
    resolve_reference,
    run_batch,
)
from ..pipeline.export import write_game, write_json
from ..pipeline.types import (
    FetchFailure,
    FetchOutcome,
    GameOutcome,
    GameRef,
    JSONObject,
    Request,
)
from ..seasons import current_season_id
from .common import (
    _extract_season_start_year,
    _fetch_baskethotel_schedule_game_ids,
    _get_baskethotel_league_id,
    _resolve_baskethotel_season_id,
    available_season_rows,
)


def fetch_canonical_season(
    season_id: str,
    category_id: str,
    components: tuple[Component, ...],
    *,
    cache_dir: Path | None = None,
    limit_games: int | None = None,
    max_workers: int = 5,
) -> BatchReport:
    """Fetch canonical games into memory without writing exports."""
    year = _extract_season_start_year(season_id)
    historical = year is not None and year <= 2023
    league_id: str | None = None
    resolved_season: str | None = None
    if historical:
        league_id = _get_baskethotel_league_id(category_id)
        resolved_season = (
            _resolve_baskethotel_season_id(season_id, league_id) or season_id
        )
        ids = _fetch_baskethotel_schedule_game_ids(resolved_season, league_id, False)
        played = [{"match_id": game_id} for game_id in ids]
    else:
        matches = BasketFiParser.extract_matches(
            BasketFiAPI.get_matches(competition_id=season_id, category_id=category_id)
        )
        played = [row for row in matches if BasketFiParser.is_match_played(row)]
    if limit_games is not None:
        if limit_games < 0:
            raise ValueError("limit_games must be nonnegative")
        played = sorted(
            played, key=lambda row: str(row.get("date") or ""), reverse=True
        )[:limit_games]

    def work(row: JSONObject) -> GameOutcome:
        if historical:
            return fetch_game(
                resolve_reference(
                    str(row["match_id"]),
                    provider=Provider.BASKETHOTEL,
                    league_id=league_id,
                    season_id=resolved_season,
                ),
                components,
                cache_dir=cache_dir,
            )
        source_id = row.get("match_external_id")
        if not isinstance(source_id, str) or not source_id:
            reference = GameRef(Provider.UNRESOLVED, str(row.get("match_id") or ""))
            request = Request("source", "", components)
            return GameOutcome(
                reference,
                components,
                (
                    FetchOutcome(
                        request,
                        failure=FetchFailure(
                            "unsupported", "Match has no supported external source"
                        ),
                    ),
                ),
            )
        return fetch_game(
            resolve_reference(source_id),
            components,
            basketfi=row,
            cache_dir=cache_dir,
        )

    return BatchReport(
        run_batch(
            [cast(JSONObject, row) for row in played], work, max_workers=max_workers
        )
    )


def download_canonical_season(
    season_id: str,
    category_id: str,
    directory: Path,
    components: tuple[Component, ...],
    *,
    binary: bool = False,
    limit_games: int | None = None,
    max_workers: int = 5,
) -> BatchReport:
    report = fetch_canonical_season(
        season_id,
        category_id,
        components,
        cache_dir=directory / ".cache" / season_id,
        limit_games=limit_games,
        max_workers=max_workers,
    )
    for outcome in report.outcomes:
        if outcome.game is not None:
            name = outcome.reference.source_id
            write_game(
                directory / f"game_{name}{'.pb' if binary else '.pb.json'}",
                outcome.game,
                binary=binary,
            )
    write_json(
        directory / "report.json",
        {
            "season_id": season_id,
            "category_id": category_id,
            "requested_games": len(report.outcomes),
            "failed_games": report.failed,
            "failures": [
                {
                    "game": o.reference.key,
                    "conversion": o.conversion_failure,
                    "validation": o.issues,
                    "fetches": [
                        f.failure.message for f in o.fetches if f.failure is not None
                    ],
                }
                for o in report.outcomes
                if not o.complete
            ],
        },
    )
    return report


def download_canonical_league(
    category_id: str,
    directory: Path,
    components: tuple[Component, ...],
    *,
    binary: bool = False,
    limit_games: int | None = None,
    max_workers: int = 5,
) -> BatchReport:
    category = BasketFiAPI.get_category(current_season_id(), category_id).get(
        "category", {}
    )
    seasons = available_season_rows(
        category.get("seasons", []), category_id
    )
    if not seasons:
        raise ValueError(f"No seasons found for category {category_id}")
    outcomes: list[GameOutcome] = []
    seen: set[str] = set()
    for season in seasons:
        identifier = str(
            season.get("competition_id")
            or season.get("season_name")
            or season.get("season_id")
            or ""
        )
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        report = download_canonical_season(
            identifier,
            category_id,
            directory / identifier,
            components,
            binary=binary,
            limit_games=limit_games,
            max_workers=max_workers,
        )
        outcomes.extend(report.outcomes)
    return BatchReport(tuple(outcomes))
