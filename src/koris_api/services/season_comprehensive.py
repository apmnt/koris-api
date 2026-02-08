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
from .common import (
    _extract_season_start_year,
    _fetch_historical_matches,
    _get_genius_session,
    _is_historical_season,
)


def download_season_comprehensive(
    category_id: str,
    competition_id: str,
    output_file: str,
    season_name: Optional[str] = None,
    include_advanced: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download all teams with their matches from one season.

    Optionally includes player data from advanced boxscores (if --advanced flag is used).
    Player data comes from match boxscores, not separate player downloads.

    This fetches:
    - All matches for the season (played matches only)
    - All teams with full rosters and staff
    - Advanced box scores with player stats per match (optional)

    All data is saved to a single structured JSON file.

    Args:
        category_id: The category/league identifier (e.g., "4" for Korisliiga)
        competition_id: The competition/season identifier (e.g., "huki2526")
        output_file: Path where output file will be saved
        season_name: Optional season name (e.g., "2024-2025") for metadata
        include_advanced: Whether to include advanced box scores with player data from Genius Sports
        max_workers: Number of concurrent workers for parallel downloads
        verbose: Whether to show progress output
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"\n{'=' * 80}")
        print("COMPREHENSIVE SEASON DATA DOWNLOAD")
        print(f"{'=' * 80}")
        print(f"Category ID: {category_id}")
        print(f"Competition ID: {competition_id}")
        if season_name:
            print(f"Season: {season_name}")
        print(f"Output file: {output_path.absolute()}")
        print(f"Include advanced stats (with player data): {include_advanced}")
        print(f"{'=' * 80}\n")

    # Get category info for category name
    if verbose:
        print("Fetching league information...")

    try:
        category_data = BasketFiAPI.get_category(competition_id, category_id)

        # Check if API returned an error
        if "call" in category_data and "error" in category_data.get("call", {}):
            # Invalid season-id, try to get available seasons with fallback
            try:
                fallback_data = BasketFiAPI.get_category("huki2526", category_id)
                if (
                    "category" in fallback_data
                    and "seasons" in fallback_data["category"]
                ):
                    category_name = fallback_data["category"].get(
                        "category_name", "Unknown"
                    )
                    seasons_list = fallback_data["category"].get("seasons", [])
                    season_year = _extract_season_start_year(competition_id)
                    if season_year is not None and season_year < 2022:
                        category_data = fallback_data
                    else:
                        print(
                            f"Error: Invalid season-id ({competition_id}) for category '{category_name}'."
                        )
                        print(f"\nAvailable seasons for category-id {category_id}:")
                        for season in seasons_list:
                            print(
                                f"  {season.get('competition_id', 'N/A'):15} - {season.get('season_name', 'Unknown')}"
                            )
                        return
            except Exception:
                pass

            # Fallback failed, show generic error
            if "category" not in category_data:
                print(f"Error: Invalid season-id ({competition_id}).")
                print("\nCommon category IDs:")
                print("  4  - Korisliiga (Men's top division)")
                print("  2  - Miesten I divisioona A (Men's 1st division A)")
                print("  13 - Naisten Korisliiga (Women's top division)")
                return

    except Exception as e:
        season_year = _extract_season_start_year(competition_id)
        if season_year is not None and season_year < 2022:
            fallback_data = BasketFiAPI.get_category("huki2526", category_id)
            category_data = fallback_data
        else:
            error_msg = "Error: Failed to fetch category/season information.\n"
            error_msg += f"This usually means the category-id ({category_id}) or season-id ({competition_id}) is invalid.\n"
            error_msg += f"Details: {str(e)}\n\n"
            error_msg += "Common category IDs:\n"
            error_msg += "  4  - Korisliiga (Men's top division)\n"
            error_msg += "  2  - Miesten I divisioona A (Men's 1st division A)\n"
            error_msg += "  13 - Naisten Korisliiga (Women's top division)\n"
            print(error_msg)
            return

    # Validate category data
    if "category" not in category_data:
        print(
            f"Error: Invalid category-id ({category_id}) or season-id ({competition_id})."
        )
        print("The API returned an empty or invalid response.")
        print("\nCommon category IDs:")
        print("  4  - Korisliiga (Men's top division)")
        print("  2  - Miesten I divisioona A (Men's 1st division A)")
        print("  13 - Naisten Korisliiga (Women's top division)")
        return

    category_name = category_data["category"].get("category_name", "Unknown")

    # Check if we got valid seasons data
    seasons_list = category_data["category"].get("seasons", [])
    if seasons_list and len(seasons_list) > 0:
        # Category is valid, check if the competition_id matches any season
        valid_competition_ids = [
            s.get("competition_id") for s in seasons_list if s.get("competition_id")
        ]
        season_year = _extract_season_start_year(competition_id)
        if competition_id not in valid_competition_ids and (
            season_year is None or season_year >= 2022
        ):
            print(
                f"Error: Invalid season-id ({competition_id}) for category '{category_name}'."
            )
            print(f"\nAvailable seasons for category-id {category_id}:")
            for season in seasons_list:
                print(
                    f"  {season.get('competition_id', 'N/A'):15} - {season.get('season_name', 'Unknown')}"
                )
            return

    season_entry = None
    if seasons_list:
        for season in seasons_list:
            if season.get("competition_id") == competition_id:
                season_entry = season
                if not season_name:
                    season_name = season.get("season_name")
                break

    if season_entry:
        is_historical = _is_historical_season(season_entry)
    else:
        season_year = _extract_season_start_year(season_name or competition_id)
        is_historical = season_year is not None and season_year < 2022

    # Additional validation - check if category name is meaningful
    if category_name == "Unknown" or not category_name:
        print(f"Warning: Category name could not be determined.")
        print(
            f"This might indicate an invalid category-id ({category_id}) or season-id ({competition_id})."
        )
        print("Continuing anyway, but results may be empty...")

    if verbose:
        print(f"✓ League: {category_name}\n")

    # Initialize comprehensive data structure
    comprehensive_data = {
        "metadata": {
            "category_id": category_id,
            "category_name": category_name,
            "competition_id": competition_id,
            "season_name": season_name or competition_id,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "include_advanced_stats": include_advanced,
        },
        "matches": [],
        "teams": [],
    }

    # Step 1: Download all matches for the season
    if verbose:
        print("Step 1: Downloading all matches for the season...")

    # Fetch all matches
    matches_data = BasketFiAPI.get_matches(
        competition_id=competition_id, category_id=category_id
    )
    matches = matches_data.get("matches", [])

    if not matches and not is_historical:
        if verbose:
            print("No matches found.")
        comprehensive_data["metadata"]["total_matches"] = 0
        comprehensive_data["metadata"]["played_matches_saved"] = 0
        processed_matches = []
    else:
        total_matches = len(matches)

        if verbose and matches:
            print(f"Found {total_matches} matches")

        # Process basic match data first
        if is_historical:
            if verbose:
                print("  - Using BasketHotel API for historical season data")
            processed_matches = _fetch_historical_matches(
                matches=matches,
                season_name=season_name or competition_id,
                season_id=str(season_entry.get("season_id")) if season_entry else None,
                category_id=category_id,
                category_name=category_name,
                max_workers=max_workers,
                verbose=verbose,
            )
            total_matches = len(processed_matches)
        else:
            processed_matches = BasketFiParser.parse_matches(
                matches, season_name=season_name or competition_id, only_played=True
            )
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
                            "match_date": match_data.get("date")
                            or match_data.get("match_date")
                            or "Unknown date",
                            "url": f"https://hosted.dcd.shared.geniussports.com/FBAA/en/match/{external_id}/boxscore",
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
                        f"HTTP {e.response.status_code}" if e.response else "HTTP Error"
                    )
                    return (match_info["index"], None, str(e), error_type)
                except ValueError as e:
                    # Parsing errors (like the int() conversion error)
                    return (match_info["index"], None, str(e), "Parse Error")
                except Exception as e:
                    error_type = type(e).__name__
                    return (match_info["index"], None, str(e), error_type)

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
                        index, boxscore, error, error_type = future.result()

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
                                }
                            if verbose and error:
                                # Show abbreviated error for common issues
                                if error_type == "Parse Error":
                                    error_display = (
                                        "Data parsing failed (check match data quality)"
                                    )
                                elif error_type and error_type.startswith("HTTP"):
                                    error_display = f"{error_type} {match_info.get('url', '')}".strip()
                                else:
                                    # For other errors, show type and short message
                                    error_display = (
                                        f"{error_type}: {error[:40]}"
                                        if error_type
                                        else error[:50]
                                    )
                                    if match_info.get("url"):
                                        error_display = (
                                            f"{error_display} {match_info['url']}"
                                        )

                                tqdm.write(
                                    f"  ✗ {match_info['match_date']} - {match_info['home_team']} vs {match_info['away_team']}: {error_display}"
                                )

                        pbar.update(1)

        comprehensive_data["matches"] = processed_matches
        comprehensive_data["metadata"]["total_matches"] = total_matches
        comprehensive_data["metadata"]["played_matches_saved"] = len(processed_matches)
        comprehensive_data["metadata"]["matches_with_advanced_stats"] = (
            matches_with_advanced
        )
        comprehensive_data["metadata"]["matches_failed"] = matches_failed

        if verbose:
            print(f"✓ Downloaded {len(processed_matches)} played matches\n")

    # Step 2: Collect all unique teams
    if verbose:
        print("Step 2: Collecting all teams from matches...")

    # Extract unique teams
    teams_list = BasketFiParser.extract_teams_from_matches(
        comprehensive_data["matches"]
    )

    if verbose:
        print(f"✓ Found {len(teams_list)} unique teams\n")

    # Step 3: Fetch detailed team data for each team
    if verbose:
        print("Step 3: Fetching detailed team data (rosters, officials, etc.)...")

    teams_with_details = []

    for idx, team_info in enumerate(teams_list, 1):
        team_id = team_info["team_id"]
        team_name = team_info["team_name"]

        if verbose:
            print(f"  [{idx}/{len(teams_list)}] Fetching {team_name}...")

        try:
            team_data = BasketFiAPI.get_team(str(team_id))
            if "team" in team_data:
                teams_with_details.append(team_data["team"])
            else:
                teams_with_details.append(team_info)
        except Exception as e:
            if verbose:
                print(f"    ✗ Error: {e}")
            teams_with_details.append({**team_info, "error": str(e)})

    comprehensive_data["teams"] = teams_with_details
    comprehensive_data["metadata"]["total_teams"] = len(teams_with_details)

    if verbose:
        print(f"✓ Fetched {len(teams_with_details)} teams\n")

    # Save everything to a single comprehensive file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(comprehensive_data, f, indent=2, ensure_ascii=False)

    # Final summary
    if verbose:
        print(f"\n{'=' * 80}")
        print("COMPREHENSIVE SEASON DOWNLOAD COMPLETE!")
        print(f"{'=' * 80}")
        print(f"League: {category_name}")
        print(f"Season: {season_name or competition_id}")
        print(f"Output file: {output_path.absolute()}")
        print(f"\nData summary:")
        print(f"  - Matches: {len(comprehensive_data['matches'])} (played matches)")
        print(f"  - Teams: {len(teams_with_details)}")
        if include_advanced:
            matches_with_player_data = sum(
                1 for m in comprehensive_data["matches"] if "boxscore" in m
            )
            print(f"  - Matches with player data: {matches_with_player_data}")
        print(f"{'=' * 80}\n")
