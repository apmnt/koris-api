"""Compatibility export of historical play-by-play through shared acquisition."""

import time
from pathlib import Path

from ..pipeline import BatchReport, Component, Provider
from ..pipeline.export import write_json
from .common import (
    _fetch_baskethotel_schedule_game_ids,
    _get_baskethotel_league_id,
    _resolve_baskethotel_season_id,
)
from .game_exports import enrich_matches, load_matches, season_reference


def download_baskethotel_season_playbyplay(
    category_id: str,
    season_id: str,
    output_file: str,
    limit_games: int | None = None,
    max_workers: int = 5,
    show_header: bool = True,
    verbose: bool = True,
) -> BatchReport:
    """Keep the legacy JSON envelope; share fetching, resume, and error policy."""
    output_path = Path(output_file)
    league_id = _get_baskethotel_league_id(category_id)
    resolved = _resolve_baskethotel_season_id(season_id, league_id) or season_id
    ids = _fetch_baskethotel_schedule_game_ids(resolved, league_id, verbose)
    if limit_games is not None:
        if limit_games < 0:
            raise ValueError("limit_games must be nonnegative")
        ids = ids[:limit_games]
    rows, report = enrich_matches(
        [{"match_id": str(game_id)} for game_id in ids],
        (Component.PLAYBYPLAY,),
        season_reference(category_id, resolved, historical=True),
        cached=load_matches(output_path),
        cache_dir=output_path.parent / ".cache" / resolved,
        max_workers=max_workers,
    )
    matches = [
        {"match_id": row["match_id"], "playbyplay": row["playbyplay"]}
        for row in rows
        if row.get("playbyplay")
    ]
    errors = [
        {"game_id": row["match_id"], "error": row.get("playbyplay_error")}
        for row in rows
        if not row.get("playbyplay")
    ]
    write_json(
        output_path,
        {
            "metadata": {
                "category_id": category_id,
                "season_id": resolved,
                "season_name": season_id,
                "source": Provider.BASKETHOTEL.value,
                "league_id": league_id,
                "total_games_requested": len(ids),
                "matches_saved": len(matches),
                "matches_failed": len(errors),
                "conversion_failures": report.failed,
                "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "matches": matches,
            "errors": errors,
        },
    )
    if verbose and show_header:
        print(
            f"Saved {len(matches)}/{len(ids)} historical play-by-play responses to {output_path}"
        )
    return report
