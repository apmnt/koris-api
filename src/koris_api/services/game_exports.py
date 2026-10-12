"""Shared application workflow for legacy season/team/UI exports."""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from ..pipeline.games import export_legacy, fetch_game, run_batch
from ..pipeline.planning import component_present, missing_components, resolve_reference
from ..pipeline.types import (
    BatchReport,
    Component,
    FetchFailure,
    FetchOutcome,
    GameOutcome,
    GameRef,
    JSONObject,
    Provider,
    Request,
)

LegacyObject = dict[str, Any]


def external_reference(row: LegacyObject) -> GameRef:
    source_id = row.get("match_external_id")
    if not source_id:
        raise ValueError(f"Match {row.get('match_id')} has no external data source")
    return resolve_reference(str(source_id))


def season_reference(
    category_id: str, season_id: str, *, historical: bool
) -> Callable[[LegacyObject], GameRef]:
    """Resolve remote season context once, before planning game requests."""
    if not historical:
        return external_reference
    from .common import _get_baskethotel_league_id, _resolve_baskethotel_season_id

    league_id = _get_baskethotel_league_id(category_id)
    resolved = _resolve_baskethotel_season_id(season_id, league_id) or season_id

    def reference_for(row: LegacyObject) -> GameRef:
        return resolve_reference(
            str(row["match_id"]),
            provider=Provider.BASKETHOTEL,
            league_id=league_id,
            season_id=resolved,
        )

    return reference_for


def load_matches(path: Path) -> dict[str, LegacyObject]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        return {}
    return {
        str(row.get("match_id")): row
        for row in payload["matches"]
        if isinstance(row, dict) and row.get("match_id")
    }


def enrich_matches(
    rows: Sequence[LegacyObject],
    components: tuple[Component, ...],
    reference_for: Callable[[LegacyObject], GameRef],
    *,
    max_workers: int = 5,
    cached: dict[str, LegacyObject] | None = None,
    cache_dir: Path | None = None,
    basketfi: dict[str, JSONObject] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[LegacyObject], BatchReport]:
    previous = cached or {}
    metadata = basketfi or {}

    def work(row: LegacyObject) -> tuple[LegacyObject, GameOutcome | None]:
        original = dict(row)
        stored = previous.get(str(row.get("match_id")), {})
        for field in (
            "boxscore",
            "playbyplay",
            "shot_chart",
            "advanced_boxscore_error",
            "playbyplay_error",
            "shot_chart_error",
            "conversion_error",
        ):
            if field in stored:
                original[field] = stored[field]
        missing = missing_components(components, cast(JSONObject, original))
        if not missing:
            return original, None
        try:
            reference = reference_for(row)
        except ValueError as exc:
            reference = GameRef(Provider.UNRESOLVED, str(row.get("match_id") or ""))
            request = Request("source", "", missing)
            outcome = GameOutcome(
                reference,
                missing,
                (FetchOutcome(request, failure=FetchFailure("unsupported", str(exc))),),
            )
            return export_legacy(outcome, original), outcome
        outcome = fetch_game(
            reference,
            missing,
            basketfi=metadata.get(str(row.get("match_id"))),
            cache_dir=cache_dir,
        )
        return export_legacy(outcome, original), outcome

    results = run_batch(rows, work, max_workers=max_workers, progress=progress)
    return [row for row, _ in results], BatchReport(
        tuple(outcome for _, outcome in results if outcome is not None)
    )


def component_counts(
    rows: Sequence[LegacyObject], components: tuple[Component, ...]
) -> dict[str, int]:
    return {
        component.value: sum(
            component_present(component, cast(JSONObject, row)) for row in rows
        )
        for component in components
    }
