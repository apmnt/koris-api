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
from ..genius_api import GeniusSportsAPI
from .baskethotel_season_boxscores import download_baskethotel_season_boxscores
from .common import (
    _augment_seasons_with_baskethotel,
    _extract_season_start_year,
    _fetch_historical_matches,
    _get_genius_session,
    _is_historical_season,
)
from .season_comprehensive import download_season_comprehensive


def download_league_boxscores_all_seasons(
    category_id: str,
    output_dir: str,
    start_year: int = 2010,
    limit_seasons: Optional[int] = None,
    combine_output: bool = False,
    combined_file: Optional[str] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download boxscores + player data for all seasons from start_year to newest.

    Historical seasons (pre-2022) are fetched from BasketHotel.
    Modern seasons (2022+) use season-comprehensive with advanced boxscores.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"\n{'=' * 80}")
        print("LEAGUE BOXSCORES (ALL SEASONS)")
        print(f"{'=' * 80}")
        print(f"Category ID: {category_id}")
        print(f"Start year: {start_year}")
        print(f"Output directory: {output_path.absolute()}")
        print(f"{'=' * 80}\n")

    try:
        category_data = BasketFiAPI.get_category("huki2526", category_id)
    except Exception as e:
        print(f"Error: Failed to fetch category info: {e}")
        return

    if "category" not in category_data or "seasons" not in category_data["category"]:
        print(f"Error: Could not retrieve seasons for category-id ({category_id}).")
        return

    category = category_data["category"]
    category_name = category.get("category_name", str(category_id))
    safe_category = (
        category_name.lower().replace(" ", "_").replace("/", "_").replace("__", "_")
    )

    seasons = category["seasons"]
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)

    filtered = []
    for season in seasons:
        season_name = season.get("season_name")
        season_year = _extract_season_start_year(season_name)
        if season_year is None or season_year < start_year:
            continue
        filtered.append(season)

    if not filtered:
        print("No seasons found for the requested year range.")
        return

    if limit_seasons is not None:
        filtered = filtered[:limit_seasons]

    if verbose:
        print(f"Found {len(filtered)} seasons to download.")

    combined_payload = {
        "metadata": {
            "category_id": category_id,
            "category_name": category_name,
            "start_year": start_year,
            "limit_seasons": limit_seasons,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "seasons": [],
    }

    for idx, season in enumerate(filtered, 1):
        season_name = season.get("season_name") or season.get("competition_id")
        competition_id = season.get("competition_id") or season.get("season_id")
        is_historical = _is_historical_season(season)

        if verbose:
            print(f"\n[{idx}/{len(filtered)}] Processing season {season_name}")

        if is_historical:
            output_file = (
                output_path / f"{safe_category}_{season_name}_baskethotel.json"
            )
            target_file = (
                output_path / f".tmp_{safe_category}_{season_name}_baskethotel.json"
                if combine_output
                else output_file
            )
            download_baskethotel_season_boxscores(
                category_id=category_id,
                season_id=season_name or str(competition_id),
                output_file=str(target_file),
                max_workers=max_workers,
                verbose=verbose,
            )
        else:
            output_file = output_path / f"{safe_category}_{season_name}_genius.json"
            target_file = (
                output_path / f".tmp_{safe_category}_{season_name}_genius.json"
                if combine_output
                else output_file
            )
            download_season_comprehensive(
                category_id=category_id,
                competition_id=str(competition_id or season_name),
                output_file=str(target_file),
                season_name=season_name,
                include_advanced=True,
                max_workers=max_workers,
                verbose=verbose,
            )

        if combine_output:
            try:
                season_payload = json.loads(target_file.read_text(encoding="utf-8"))
                season_payload["season_name"] = season_name
                season_payload["source"] = "baskethotel" if is_historical else "genius"
                combined_payload["seasons"].append(season_payload)
            finally:
                if target_file.exists():
                    target_file.unlink()

    if combine_output:
        combined_path = (
            Path(combined_file)
            if combined_file
            else output_path / f"{safe_category}_combined.json"
        )
        with open(combined_path, "w", encoding="utf-8") as f:
            json.dump(combined_payload, f, indent=2, ensure_ascii=False)
        if verbose:
            print(f"\nSaved combined output to {combined_path}")


def download_league_all_seasons(
    category_id: str,
    output_file: str,
    season_id: str = "huki2526",
    include_advanced: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """Download all matches from all seasons for a specific league/category."""

    if verbose:
        print(f"Fetching category information for category {category_id}...")

    # First, get category info to find all available seasons
    category_data = BasketFiAPI.get_category(season_id, category_id)

    if "category" not in category_data or "seasons" not in category_data["category"]:
        print("Error: Could not retrieve seasons for this category.")
        return

    seasons = category_data["category"]["seasons"]
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)
    category_name = category_data["category"].get("category_name", "Unknown")

    if not seasons:
        print("No seasons found for this category.")
        return

    if verbose:
        print(f"\nLeague: {category_name}")
        print(f"Found {len(seasons)} seasons to download")
        print(f"{'=' * 60}\n")

    # Collect all matches from all seasons
    all_matches: list[Dict[str, Any]] = []
    total_matches_found = 0
    total_played_matches = 0
    total_advanced_stats = 0
    total_failed = 0
    seasons_processed = []

    for idx, season in enumerate(seasons, 1):
        season_data_id = season["season_id"]
        season_name = season["season_name"]
        season_competition_id = season["competition_id"]
        is_historical = _is_historical_season(season)

        if verbose:
            print(f"[{idx}/{len(seasons)}] Processing season: {season_name}")

        try:
            if is_historical:
                matches = []
            else:
                matches_data = BasketFiAPI.get_matches(
                    competition_id=season_competition_id, category_id=category_id
                )
                matches = BasketFiParser.extract_matches(matches_data)
                total_matches_found += len(matches)

            # Process matches for this season
            if is_historical:
                if verbose:
                    print("  - Using BasketHotel API for historical season data")
                processed_matches_raw = _fetch_historical_matches(
                    matches=matches,
                    season_name=season_name,
                    season_id=str(season_data_id) if season_data_id else None,
                    category_id=category_id,
                    category_name=category_name,
                    max_workers=max_workers,
                    verbose=verbose,
                )
                total_matches_found += len(processed_matches_raw)
            else:
                processed_matches_raw = BasketFiParser.parse_matches(
                    matches, season_name=season_name, only_played=True
                )
            # Add season_id to each match for league-comprehensive
            processed_matches = []
            for match_data in processed_matches_raw:
                match_data_with_season = {**match_data, "season_id": season_data_id}
                processed_matches.append(match_data_with_season)

            matches_to_fetch_advanced = []

            # Check if we should fetch advanced stats
            if include_advanced:
                for idx, match_data in enumerate(processed_matches):
                    external_id = match_data.get("match_external_id")
                    if external_id:
                        matches_to_fetch_advanced.append(
                            {
                                "index": len(all_matches) + idx,
                                "external_id": external_id,
                                "home_team": match_data["home_team"],
                                "away_team": match_data["away_team"],
                            }
                        )

            total_played_matches += len(processed_matches)
            all_matches.extend(processed_matches)

            if verbose:
                if is_historical:
                    print(f"  ✓ Found {len(processed_matches)} played matches")
                else:
                    print(
                        f"  ✓ Found {len(matches)} matches, {len(processed_matches)} played"
                    )

            # Fetch advanced stats for this season if requested
            if include_advanced and matches_to_fetch_advanced:
                season_advanced = 0
                season_failed = 0

                def fetch_boxscore(
                    match_info: Dict[str, Any],
                ) -> tuple[int, Optional[Dict[str, Any]], Optional[str], Optional[str]]:
                    """Fetch box score for a single match. Returns (index, boxscore_data, error_msg, error_type)."""
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
                            f"HTTP {e.response.status_code}"
                            if e.response
                            else "HTTP Error"
                        )
                        return (match_info["index"], None, str(e), error_type)
                    except ValueError as e:
                        # Parsing errors (like the int() conversion error)
                        return (match_info["index"], None, str(e), "Parse Error")
                    except Exception as e:
                        error_type = type(e).__name__
                        return (match_info["index"], None, str(e), error_type)

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(fetch_boxscore, match_info): match_info
                        for match_info in matches_to_fetch_advanced
                    }

                    with tqdm(
                        total=len(matches_to_fetch_advanced),
                        desc=f"  Fetching advanced stats ({season_name})",
                        disable=not verbose,
                    ) as pbar:
                        for future in as_completed(futures):
                            match_info = futures[future]
                            index, boxscore, error, error_type = future.result()

                            if boxscore:
                                all_matches[index]["boxscore"] = normalize_boxscore(
                                    boxscore, source="genius"
                                )
                                season_advanced += 1
                                total_advanced_stats += 1
                            else:
                                season_failed += 1
                                total_failed += 1
                                # Add error information to match data for debugging
                                if error:
                                    all_matches[index]["advanced_boxscore_error"] = {
                                        "error_type": error_type or "Unknown",
                                        "error_message": error,
                                    }
                                if verbose and error:
                                    # Show abbreviated error for common issues
                                    if error_type == "Parse Error":
                                        error_display = "Data parsing failed"
                                    elif error_type and error_type.startswith("HTTP"):
                                        error_display = error_type
                                    else:
                                        # For other errors, show type and short message
                                        error_display = (
                                            f"{error_type}: {error[:35]}"
                                            if error_type
                                            else error[:45]
                                        )

                                    tqdm.write(
                                        f"    ✗ {match_info['home_team']} vs {match_info['away_team']}: {error_display}"
                                    )

                            pbar.update(1)

                if verbose:
                    stats_msg = f"  ✓ Advanced stats: {season_advanced}/{len(matches_to_fetch_advanced)}"
                    if season_failed > 0:
                        stats_msg += f" ({season_failed} failed)"
                    print(stats_msg)

            seasons_processed.append(
                {
                    "season_id": season_data_id,
                    "season_name": season_name,
                    "competition_id": season_competition_id,
                    "total_matches": len(processed_matches_raw)
                    if is_historical
                    else len(matches),
                    "played_matches": len(processed_matches),
                }
            )

        except Exception as e:
            if verbose:
                print(f"  ✗ Error processing season {season_name}: {str(e)}")
            continue

    # Save all matches to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "metadata": {
            "category_id": category_id,
            "category_name": category_name,
            "total_seasons": len(seasons),
            "seasons_processed": len(seasons_processed),
            "total_matches_found": total_matches_found,
            "total_played_matches_saved": total_played_matches,
            "matches_with_advanced_stats": total_advanced_stats,
            "matches_failed": total_failed,
            "include_advanced_stats": include_advanced,
        },
        "seasons": seasons_processed,
        "matches": all_matches,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(
            f"✓ Successfully saved {total_played_matches} played matches to {output_path}"
        )
        print(f"  - League: {category_name}")
        print(f"  - Seasons processed: {len(seasons_processed)}/{len(seasons)}")
        print(f"  - Total matches found: {total_matches_found}")
        print(f"  - Played matches saved: {total_played_matches}")
        if include_advanced:
            print(f"  - Advanced stats: {total_advanced_stats} matches")
            if total_failed > 0:
                print(f"  - Failed: {total_failed}")
                # Count error types
                error_types: Dict[str, int] = {}
                for match in all_matches:
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
