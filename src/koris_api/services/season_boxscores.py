import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, cast

import requests
from tqdm import tqdm

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..boxscore_normalizer import normalize_boxscore
from ..genius_api import GeniusSportsAPI
from ..pipeline.export import write_json
from ..pipeline.planning import resolve_reference
from ..pipeline.types import BatchReport, Component, GameRef, JSONObject
from ..sportradar_api import is_sportradar_id
from .common import _get_genius_session, resolve_genius_competition_id
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


def download_matches_with_boxscores(
    season_id: str,
    category_id: str,
    output_file: str,
    include_advanced: bool = False,
    include_playbyplay: bool = False,
    include_shot_chart: bool = False,
    limit_games: int | None = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> BatchReport:
    """Fetch a season through the shared game pipeline; preserve legacy JSON."""
    if limit_games is not None and limit_games < 0:
        raise ValueError("limit_games must be nonnegative")
    data = BasketFiAPI.get_matches(competition_id=season_id, category_id=category_id)
    matches = BasketFiParser.extract_matches(data)
    rows = BasketFiParser.parse_matches(matches, only_played=True)
    if limit_games is not None:
        rows = sorted(rows, key=lambda row: str(row.get("date") or ""), reverse=True)[
            :limit_games
        ]
    requested = tuple(
        component
        for component, enabled in (
            (Component.BOXSCORE, include_advanced),
            (Component.PLAYBYPLAY, include_playbyplay),
            (Component.SHOTS, include_shot_chart),
        )
        if enabled
    )
    output_path = Path(output_file)
    raw = {str(row.get("match_id")): cast(JSONObject, row) for row in matches}

    def reference_for(row: dict[str, Any]) -> GameRef:
        source_id = row.get("match_external_id")
        if not source_id:
            raise ValueError(f"Match {row.get('match_id')} has no external data source")
        return resolve_reference(str(source_id))

    def update_progress(completed: int, total: int) -> None:
        _ = progress.update(completed - progress.n)

    with tqdm(total=len(rows), desc="Fetching games", disable=not verbose) as progress:
        processed, report = enrich_matches(
            rows,
            requested,
            reference_for,
            max_workers=max_workers,
            cached=load_matches(output_path),
            basketfi=raw,
            cache_dir=output_path.parent / ".cache" / season_id,
            progress=update_progress,
        )
    counts = component_counts(processed, requested)
    metadata: dict[str, Any] = {
        "category_id": category_id,
        "season_id": season_id,
        "season_name": season_id,
        "source": "sportradar"
        if processed
        and all(
            is_sportradar_id(str(m.get("match_external_id") or "")) for m in processed
        )
        else "genius",
        "league_id": None,
        "total_matches_in_season": len(matches),
        "total_games_requested": limit_games,
        "played_matches_saved": len(processed),
        "limit_games": limit_games,
        "include_advanced_stats": include_advanced,
        "include_playbyplay": include_playbyplay,
        "include_shot_chart": include_shot_chart,
        "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "matches_with_boxscore": counts.get("boxscore", 0),
        "matches_failed": len(processed) - counts.get("boxscore", 0)
        if include_advanced
        else 0,
        "matches_with_playbyplay": counts.get("playbyplay", 0),
        "matches_playbyplay_failed": len(processed) - counts.get("playbyplay", 0)
        if include_playbyplay
        else 0,
        "matches_with_shot_chart": counts.get("shot_chart", 0),
        "matches_shot_chart_failed": len(processed) - counts.get("shot_chart", 0)
        if include_shot_chart
        else 0,
        "conversion_failures": report.failed,
    }
    write_json(output_path, {"metadata": metadata, "matches": processed})
    if verbose:
        print(f"Saved {len(processed)} games to {output_path}")
        for component in requested:
            print(f"  {component.value}: {counts[component.value]}/{len(processed)}")
    return report


def _is_retryable_404_error(error: dict[str, Any]) -> bool:
    error_type = str(error.get("error_type", ""))
    error_message = str(error.get("error_message", ""))
    return "404" in error_type or "404" in error_message


def retry_advanced_boxscores_404s(
    input_file: str,
    output_file: str | None = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """Retry advanced boxscores that previously failed with HTTP 404."""
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}")
        return

    data = json.loads(input_path.read_text(encoding="utf-8"))
    matches = data.get("matches") or []
    if not isinstance(matches, list) or not matches:
        print("No matches found in input file.")
        return

    matches_to_retry = []
    for idx, match in enumerate(matches):
        if match.get("boxscore"):
            continue
        error = match.get("advanced_boxscore_error")
        external_id = match.get("match_external_id")
        if not error or not external_id:
            continue
        if _is_retryable_404_error(error):
            matches_to_retry.append(
                {
                    "index": idx,
                    "external_id": external_id,
                    "competition_id": resolve_genius_competition_id(
                        category_id=match.get("category_id")
                        or data.get("metadata", {}).get("category_id"),
                        season_id=match.get("season")
                        or data.get("metadata", {}).get("season_id"),
                        match_category_external_id=match.get("category_external_id"),
                    ),
                    "home_team": match.get("home_team"),
                    "away_team": match.get("away_team"),
                }
            )

    if not matches_to_retry:
        print("No 404 advanced boxscore failures to retry.")
        return

    if output_file:
        output_path = Path(output_file)
    else:
        output_path = input_path.with_name(
            f"{input_path.stem}_retry{input_path.suffix}"
        )

    if verbose:
        print(
            f"Retrying {len(matches_to_retry)} advanced boxscores from {input_path}..."
        )

    retried_success = 0
    retried_failed = 0

    def fetch_boxscore(
        match_info: dict[str, Any],
    ) -> tuple[int, dict[str, Any] | None, str | None, str | None]:
        try:
            session = _get_genius_session(max_workers)
            boxscore = GeniusSportsAPI.get_match_boxscore(
                str(match_info["external_id"]),
                competition_id=match_info.get("competition_id"),
                session=session,
                log_fn=tqdm.write if verbose else None,
            )
            return (match_info["index"], boxscore, None, None)
        except requests.exceptions.HTTPError as e:
            error_type = (
                f"HTTP {e.response.status_code}" if e.response else "HTTP Error"
            )
            return (match_info["index"], None, str(e), error_type)
        except ValueError as e:
            return (match_info["index"], None, str(e), "Parse Error")
        except Exception as e:
            logging.getLogger(__name__).debug(
                "season_boxscores: operation failed", exc_info=True
            )
            return (match_info["index"], None, str(e), type(e).__name__)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_boxscore, match_info): match_info
            for match_info in matches_to_retry
        }

        with tqdm(
            total=len(matches_to_retry),
            desc="Retrying advanced stats",
            disable=not verbose,
        ) as pbar:
            for future in as_completed(futures):
                match_info = futures[future]
                index, boxscore, error, error_type = future.result()
                if boxscore:
                    matches[index]["boxscore"] = normalize_boxscore(
                        boxscore, source="genius"
                    )
                    matches[index].pop("advanced_boxscore_error", None)
                    retried_success += 1
                else:
                    retried_failed += 1
                    if error:
                        matches[index]["advanced_boxscore_error"] = {
                            "error_type": error_type or "Unknown",
                            "error_message": error,
                        }
                    if verbose and error:
                        if error_type == "Parse Error":
                            error_display = (
                                "Data parsing failed (check match data quality)"
                            )
                        elif error_type and error_type.startswith("HTTP"):
                            error_display = error_type
                        else:
                            error_display = (
                                f"{error_type}: {error[:40]}"
                                if error_type
                                else error[:50]
                            )
                        tqdm.write(
                            f"  ✗ {match_info.get('home_team')} vs "
                            f"{match_info.get('away_team')}: {error_display}"
                        )
                pbar.update(1)

    matches_with_advanced = sum(1 for match in matches if "boxscore" in match)
    matches_failed = sum(1 for match in matches if "advanced_boxscore_error" in match)

    metadata = data.get("metadata", {})
    metadata["matches_with_boxscore"] = matches_with_advanced
    metadata["matches_failed"] = matches_failed
    metadata["retry_advanced_boxscores"] = {
        "retry_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "retry_total": len(matches_to_retry),
        "retry_success": retried_success,
        "retry_failed": retried_failed,
    }
    data["metadata"] = metadata
    data["matches"] = matches

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Saved retried data to {output_path}")
        print(f"  - Retried: {len(matches_to_retry)}")
        print(f"  - Retried success: {retried_success}")
        print(f"  - Retried failed: {retried_failed}")
        print(f"  - Matches with boxscore: {matches_with_advanced}")
        print(f"  - Matches failed: {matches_failed}")
        print(f"{'=' * 60}")
