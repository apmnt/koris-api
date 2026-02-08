import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any

import requests
from tqdm import tqdm

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..boxscore_normalizer import normalize_boxscore
from ..genius_api import GeniusSportsAPI, GeniusSportsBoxscoreError
from .common import _get_genius_session


def download_matches_with_boxscores(
    season_id: str,
    category_id: str,
    output_file: str,
    include_advanced: bool = False,
    limit_games: Optional[int] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """Download all matches for a season, optionally including advanced box scores."""

    if verbose:
        print(f"Fetching matches for season {season_id}, category {category_id}...")

    # Fetch all matches
    data = BasketFiAPI.get_matches(competition_id=season_id, category_id=category_id)
    matches = BasketFiParser.extract_matches(data)

    if not matches:
        print("No matches found.")
        return

    total_matches = len(matches)
    if verbose:
        print(f"Found {total_matches} matches")

    # Process basic match data first
    processed_matches = BasketFiParser.parse_matches(matches, only_played=True)
    if limit_games is not None and include_advanced:
        candidate_limit = max(limit_games * 30, limit_games)
        processed_matches = processed_matches[:candidate_limit]
    elif limit_games is not None:
        processed_matches = processed_matches[:limit_games]
    matches_to_fetch_advanced = []

    # Check if we should fetch advanced stats for matches
    if include_advanced:
        for idx, match_data in enumerate(processed_matches):
            external_id = match_data.get("match_external_id")
            if external_id:
                matches_to_fetch_advanced.append(
                    {
                        "index": idx,
                        "external_id": external_id,
                        "home_team": match_data["home_team"],
                        "away_team": match_data["away_team"],
                    }
                )

    # Fetch advanced stats concurrently if requested
    matches_with_advanced = 0
    matches_failed = 0

    if include_advanced and matches_to_fetch_advanced:
        if verbose:
            print(
                f"\nFetching advanced box scores for {len(matches_to_fetch_advanced)} played matches..."
            )

        def fetch_boxscore(
            match_info: Dict[str, Any],
        ) -> tuple[
            int,
            Optional[Dict[str, Any]],
            Optional[str],
            Optional[str],
            Optional[str],
            Optional[int],
        ]:
            """
            Fetch box score for a single match.
            Returns (index, boxscore_data, error_msg, error_type, error_url, status_code).
            """
            try:
                session = _get_genius_session(max_workers)
                boxscore = GeniusSportsAPI.get_match_boxscore(
                    str(match_info["external_id"]),
                    session=session,
                    log_fn=tqdm.write if verbose else None,
                )
                return (match_info["index"], boxscore, None, None, None, None)
            except GeniusSportsBoxscoreError as e:
                error_type = e.error_type or "GeniusSportsError"
                status_code = e.status_code
                return (
                    match_info["index"],
                    None,
                    str(e),
                    error_type,
                    e.url,
                    status_code,
                )
            except requests.exceptions.HTTPError as e:
                error_type = (
                    f"HTTP {e.response.status_code}" if e.response else "HTTP Error"
                )
                status_code = e.response.status_code if e.response else None
                return (match_info["index"], None, str(e), error_type, None, status_code)
            except ValueError as e:
                # Parsing errors (like the int() conversion error)
                return (match_info["index"], None, str(e), "Parse Error", None, None)
            except Exception as e:
                error_type = type(e).__name__
                return (match_info["index"], None, str(e), error_type, None, None)

        # Use ThreadPoolExecutor for concurrent fetching
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(fetch_boxscore, match_info): match_info
                for match_info in matches_to_fetch_advanced
            }

            # Process results with progress bar
            with tqdm(
                total=len(matches_to_fetch_advanced),
                desc="Fetching advanced stats",
                disable=not verbose,
            ) as pbar:
                for future in as_completed(futures):
                    match_info = futures[future]
                    (
                        index,
                        boxscore,
                        error,
                        error_type,
                        error_url,
                        status_code,
                    ) = future.result()

                    if boxscore:
                        processed_matches[index]["boxscore"] = normalize_boxscore(
                            boxscore, source="genius"
                        )
                        matches_with_advanced += 1
                    else:
                        matches_failed += 1
                        # Add error information to match data for debugging
                        if error:
                            processed_matches[index]["advanced_boxscore_error"] = {
                                "error_type": error_type or "Unknown",
                                "error_message": error,
                                "status_code": status_code,
                                "url": error_url,
                                "match_external_id": match_info.get("external_id"),
                            }
                        if verbose and error:
                            # Show abbreviated error for common issues
                            if error_type == "Parse Error":
                                error_display = (
                                    "Data parsing failed (check match data quality)"
                                )
                            elif status_code is not None:
                                error_display = f"HTTP {status_code}"
                            elif error_type and error_type.startswith("HTTP"):
                                error_display = error_type
                            else:
                                # For other errors, show type and short message
                                error_display = (
                                    f"{error_type}: {error[:40]}"
                                    if error_type
                                    else error[:50]
                                )

                            url_display = f" @ {error_url}" if error_url else ""
                            tqdm.write(
                                f"  ✗ {match_info['home_team']} vs {match_info['away_team']}: "
                                f"{error_display}{url_display}"
                            )

                    pbar.update(1)

    if limit_games is not None and include_advanced:

        def has_player_stats(match: Dict[str, Any]) -> bool:
            boxscore = match.get("boxscore") or {}
            teams = boxscore.get("teams") or []
            return any(team.get("players") for team in teams)

        preferred = [m for m in processed_matches if has_player_stats(m)]
        if len(preferred) >= limit_games:
            processed_matches = preferred[:limit_games]
        else:
            preferred_ids = {m.get("match_id") for m in preferred}
            remainder = [
                m for m in processed_matches if m.get("match_id") not in preferred_ids
            ]
            processed_matches = (preferred + remainder)[:limit_games]

    matches_with_advanced = sum(1 for match in processed_matches if "boxscore" in match)
    matches_failed = sum(
        1 for match in processed_matches if "advanced_boxscore_error" in match
    )

    # Save to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "metadata": {
            "category_id": category_id,
            "season_id": season_id,
            "season_name": season_id,
            "source": "genius",
            "league_id": None,
            "total_matches_in_season": total_matches,
            "total_games_requested": None,
            "played_matches_saved": len(processed_matches),
            "matches_with_boxscore": matches_with_advanced,
            "matches_failed": matches_failed,
            "include_advanced_stats": include_advanced,
            "limit_games": limit_games,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "matches": processed_matches,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(
            f"Successfully saved {len(processed_matches)} played matches to {output_path}"
        )
        print(f"  - Total matches in season: {total_matches}")
        print(f"  - Played matches saved: {len(processed_matches)}")
        if include_advanced:
            print(
                f"  - Advanced stats: {matches_with_advanced}/{len(matches_to_fetch_advanced)} matches"
            )
            if matches_failed > 0:
                print(f"  - Failed: {matches_failed}")
                # Count error types
                error_types: Dict[str, int] = {}
                for match in processed_matches:
                    if "advanced_boxscore_error" in match:
                        err_type = match["advanced_boxscore_error"].get(
                            "error_type", "Unknown"
                        )
                        error_types[err_type] = error_types.get(err_type, 0) + 1

                if error_types:
                    print("  - Error breakdown:")
                    for err_type, count in sorted(
                        error_types.items(), key=lambda x: -x[1]
                    ):
                        print(f"    • {err_type}: {count}")
        print(f"{'=' * 60}")


def _is_retryable_404_error(error: Dict[str, Any]) -> bool:
    error_type = str(error.get("error_type", ""))
    error_message = str(error.get("error_message", ""))
    return "404" in error_type or "404" in error_message


def retry_advanced_boxscores_404s(
    input_file: str,
    output_file: Optional[str] = None,
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
        match_info: Dict[str, Any],
    ) -> tuple[int, Optional[Dict[str, Any]], Optional[str], Optional[str]]:
        try:
            session = _get_genius_session(max_workers)
            boxscore = GeniusSportsAPI.get_match_boxscore(
                str(match_info["external_id"]),
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
