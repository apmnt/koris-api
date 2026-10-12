import time
from pathlib import Path
from typing import Any

from ..pipeline.export import write_json
from ..pipeline.planning import resolve_reference
from ..pipeline.types import BatchReport, Component, GameRef, Provider
from .common import (
    _fetch_baskethotel_schedule_game_ids,
    _get_baskethotel_league_id,
    _resolve_baskethotel_season_id,
)
from .game_exports import component_counts, enrich_matches, load_matches


def _attach_playbyplay_possession_counts(match_data: dict[str, Any]) -> None:
    playbyplay = match_data.get("playbyplay")
    if not isinstance(playbyplay, dict):
        match_data["home_number_of_possessions"] = None
        match_data["away_number_of_possessions"] = None
        return

    possessions = playbyplay.get("possessions")
    if not isinstance(possessions, dict):
        match_data["home_number_of_possessions"] = None
        match_data["away_number_of_possessions"] = None
        return

    match_data["home_number_of_possessions"] = possessions.get("team1_possessions")
    match_data["away_number_of_possessions"] = possessions.get("team2_possessions")


def download_baskethotel_season_boxscores(
    category_id: str,
    season_id: str,
    output_file: str,
    limit_games: int | None = None,
    max_workers: int = 5,
    include_playbyplay: bool = False,
    show_header: bool = True,
    verbose: bool = True,
    include_shot_chart: bool = False,
) -> BatchReport:
    """Fetch historical games through the same workflow as modern seasons."""
    if limit_games is not None and limit_games < 0:
        raise ValueError("limit_games must be nonnegative")
    league_id = _get_baskethotel_league_id(category_id)
    resolved = _resolve_baskethotel_season_id(season_id, league_id) or season_id
    game_ids = _fetch_baskethotel_schedule_game_ids(str(resolved), league_id, verbose)
    if limit_games is not None:
        game_ids = list(game_ids)[:limit_games]
    rows = [
        {
            "match_id": str(game_id),
            "match_external_id": None,
            "category": None,
            "competition": None,
            "season": season_id,
        }
        for game_id in game_ids
    ]
    components = (
        (Component.BOXSCORE, Component.PLAYBYPLAY)
        if include_playbyplay
        else (Component.BOXSCORE,)
    )
    if include_shot_chart:
        components += (Component.SHOTS,)
    output_path = Path(output_file)

    def reference_for(row: dict[str, Any]) -> GameRef:
        return resolve_reference(
            str(row["match_id"]),
            provider=Provider.BASKETHOTEL,
            league_id=league_id,
            season_id=str(resolved),
        )

    matches, report = enrich_matches(
        rows,
        components,
        reference_for,
        max_workers=max_workers,
        cached=load_matches(output_path),
        cache_dir=output_path.parent / ".cache" / str(resolved),
    )
    counts = component_counts(matches, components)
    errors = [
        {
            "game_id": row["match_id"],
            "error": row["advanced_boxscore_error"]["error_message"],
        }
        for row in matches
        if row.get("advanced_boxscore_error")
    ]
    successful = [row for row in matches if row.get("boxscore")]
    write_json(
        output_path,
        {
            "metadata": {
                "category_id": category_id,
                "season_id": str(resolved),
                "season_name": season_id,
                "source": "baskethotel",
                "league_id": league_id,
                "total_matches_in_season": None,
                "total_games_requested": len(game_ids),
                "played_matches_saved": len(successful),
                "matches_with_boxscore": counts.get("boxscore", 0),
                "matches_failed": len(errors),
                "matches_with_playbyplay": counts.get("playbyplay", 0),
                "matches_playbyplay_failed": len(game_ids) - counts.get("playbyplay", 0)
                if include_playbyplay
                else 0,
                "include_advanced_stats": True,
                "include_playbyplay": include_playbyplay,
                "include_shot_chart": include_shot_chart,
                "matches_shot_chart_failed": len(game_ids) if include_shot_chart else 0,
                "limit_games": limit_games,
                "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "conversion_failures": report.failed,
            },
            "matches": successful,
            "errors": errors,
        },
    )
    if verbose and show_header:
        print(
            f"Saved {len(successful)}/{len(game_ids)} games to {output_path}; {len(errors)} failed"
        )
    return report
