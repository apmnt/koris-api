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
    _augment_seasons_with_baskethotel,
    _fetch_historical_matches,
    _get_genius_session,
    _is_historical_season,
    resolve_genius_competition_id,
)


def download_league_comprehensive(
    category_id: str,
    output_dir: str,
    season_id: str = "huki2526",
    include_advanced: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download all seasons with all teams and their matches.

    Optionally includes player data from advanced boxscores (if --advanced flag is used).
    Player data comes from match boxscores, not separate player downloads.

    This fetches:
    - All seasons for the league
    - For each season:
      - All matches (played matches only)
      - All teams that participated in that season
      - Team details including rosters and staff (current data from API)

    Note: Team data (rosters, officials) is fetched from the current API state.
    The API does not provide historical team rosters, so team details reflect
    the current state at download time, not historical rosters from each season.

    All data is organized by season and saved to a single structured JSON file.

    Args:
        category_id: The category/league identifier (e.g., "4" for Korisliiga)
        output_dir: Directory where output file will be saved
        season_id: A season ID to use for fetching category info (default: huki2526)
        include_advanced: Whether to include advanced box scores with player data from Genius Sports
        max_workers: Number of concurrent workers for parallel downloads
        verbose: Whether to show progress output
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"\n{'=' * 80}")
        print("COMPREHENSIVE LEAGUE DATA DOWNLOAD")
        print(f"{'=' * 80}")
        print(f"Category ID: {category_id}")
        print(f"Output directory: {output_path.absolute()}")
        print(f"Include advanced stats (with player data): {include_advanced}")
        print(f"{'=' * 80}\n")

    # Step 1: Get category info and all seasons
    if verbose:
        print("Step 1: Fetching league information and seasons...")

    try:
        category_data = BasketFiAPI.get_category(season_id, category_id)
    except Exception as e:
        error_msg = "Error: Failed to fetch category/season information.\n"
        error_msg += f"This usually means the category-id ({category_id}) or reference season-id ({season_id}) is invalid.\n"
        error_msg += f"Details: {str(e)}\n\n"
        error_msg += "Common category IDs:\n"
        error_msg += "  4  - Korisliiga (Men's top division)\n"
        error_msg += "  2  - Miesten I divisioona A (Men's 1st division A)\n"
        error_msg += "  13 - Naisten Korisliiga (Women's top division)\n"
        print(error_msg)
        return

    if "category" not in category_data or "seasons" not in category_data["category"]:
        print(f"Error: Could not retrieve seasons for category-id ({category_id}).")
        print(
            f"This usually means the category-id or the reference season-id ({season_id}) is invalid."
        )
        print("\nCommon category IDs:")
        print("  4  - Korisliiga (Men's top division)")
        print("  2  - Miesten I divisioona A (Men's 1st division A)")
        print("  13 - Naisten Korisliiga (Women's top division)")
        return

    category = category_data["category"]
    seasons = category["seasons"]
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)
    category_name = category.get("category_name", "Unknown")

    # Validate category name
    if category_name == "Unknown" or not category_name:
        print("Warning: Category name could not be determined.")
        print(f"This might indicate an invalid category-id ({category_id}).")
        print("Continuing anyway, but results may be empty...")

    if not seasons:
        print(f"No seasons found for category-id ({category_id}).")
        print("This category might not have any active seasons.")
        return

    if verbose:
        print(f"✓ League: {category_name}")
        print(f"✓ Found {len(seasons)} seasons")
        print(f"\nAvailable seasons:")
        for season in seasons:
            print(
                f"  {season.get('competition_id', 'N/A'):15} - {season.get('season_name', 'Unknown')}"
            )
        print()

    # Initialize comprehensive data structure
    comprehensive_data = {
        "metadata": {
            "category_id": category_id,
            "category_name": category_name,
            "total_seasons": len(seasons),
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "include_advanced_stats": include_advanced,
        },
        "seasons": [],
    }

    # Step 2: Download all matches for all seasons and organize by season
    if verbose:
        print("Step 2: Downloading matches and teams for each season...")

    # Process each season separately
    total_matches_found = 0
    total_played_matches = 0
    total_advanced_stats = 0
    total_failed = 0
    total_teams_fetched = 0

    for idx, season in enumerate(seasons, 1):
        season_data_id = season["season_id"]
        season_name = season["season_name"]
        season_competition_id = season["competition_id"]
        is_historical = _is_historical_season(season)

        if verbose:
            print(f"  [{idx}/{len(seasons)}] Processing season: {season_name}")

        season_data = {
            "season_id": season_data_id,
            "season_name": season_name,
            "competition_id": season_competition_id,
            "matches": [],
            "teams": [],
        }

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
                    print("    - Using BasketHotel API for historical season data")
                processed_matches = _fetch_historical_matches(
                    matches=matches,
                    season_name=season_name,
                    season_id=str(season_data_id) if season_data_id else None,
                    category_id=category_id,
                    category_name=category_name,
                    max_workers=max_workers,
                    verbose=verbose,
                )
                total_matches_found += len(processed_matches)
            else:
                processed_matches = BasketFiParser.parse_matches(
                    matches, season_name=season_name, only_played=True
                )

            matches_to_fetch_advanced = []

            # Check if we should fetch advanced stats
            if include_advanced:
                for match_idx, match_data in enumerate(processed_matches):
                    external_id = match_data.get("match_external_id")
                    if external_id:
                        competition_id_resolved = resolve_genius_competition_id(
                            category_id=category_id,
                            season_id=season_name,
                            match_category_external_id=match_data.get(
                                "category_external_id"
                            ),
                        )
                        matches_to_fetch_advanced.append(
                            {
                                "index": match_idx,
                                "external_id": external_id,
                                "competition_id": competition_id_resolved,
                                "home_team": match_data["home_team"],
                                "away_team": match_data["away_team"],
                                "match_date": match_data.get("date")
                                or match_data.get("match_date")
                                or "Unknown date",
                                "url": GeniusSportsAPI.build_match_boxscore_url(
                                    str(external_id),
                                    competition_id=competition_id_resolved,
                                ),
                            }
                        )

            total_played_matches += len(processed_matches)

            if verbose:
                if is_historical:
                    print(f"    ✓ Found {len(processed_matches)} played matches")
                else:
                    print(
                        f"    ✓ Found {len(matches)} matches, {len(processed_matches)} played"
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
                            competition_id=match_info.get("competition_id"),
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
                        desc=f"    Fetching advanced stats ({season_name})",
                        disable=not verbose,
                    ) as pbar:
                        for future in as_completed(futures):
                            match_info = futures[future]
                            index, boxscore, error, error_type = future.result()

                            if boxscore:
                                processed_matches[index]["boxscore"] = (
                                    normalize_boxscore(boxscore, source="genius")
                                )
                                season_advanced += 1
                                total_advanced_stats += 1
                            else:
                                season_failed += 1
                                total_failed += 1
                                # Add error information to match data for debugging
                                if error:
                                    processed_matches[index][
                                        "advanced_boxscore_error"
                                    ] = {
                                        "error_type": error_type or "Unknown",
                                        "error_message": error,
                                    }
                                if verbose and error:
                                    # Show abbreviated error for common issues
                                    if error_type == "Parse Error":
                                        error_display = "Data parsing failed"
                                    elif error_type and error_type.startswith("HTTP"):
                                        error_display = f"{error_type} {match_info.get('url', '')}".strip()
                                    else:
                                        # For other errors, show type and short message
                                        error_display = (
                                            f"{error_type}: {error[:35]}"
                                            if error_type
                                            else error[:45]
                                        )
                                        if match_info.get("url"):
                                            error_display = (
                                                f"{error_display} {match_info['url']}"
                                            )

                                    tqdm.write(
                                        f"      ✗ {match_info['match_date']} - {match_info['home_team']} vs {match_info['away_team']}: {error_display}"
                                    )

                            pbar.update(1)

                if verbose:
                    stats_msg = f"    ✓ Advanced stats: {season_advanced}/{len(matches_to_fetch_advanced)}"
                    if season_failed > 0:
                        stats_msg += f" ({season_failed} failed)"
                    print(stats_msg)

            # Store matches for this season
            season_data["matches"] = processed_matches

            # Extract unique teams for this season
            teams_list = BasketFiParser.extract_teams_from_matches(processed_matches)

            if verbose:
                print(f"    ✓ Found {len(teams_list)} teams in this season")
                print(f"    Fetching team details for season {season_name}...")

            # Fetch detailed team data for each team in this season
            teams_with_details = []

            for team_idx, team_info in enumerate(teams_list, 1):
                team_id = team_info["team_id"]
                team_name = team_info["team_name"]

                if verbose:
                    print(
                        f"\r\033[2K      [{team_idx}/{len(teams_list)}] Fetching {team_name}...",
                        end="",
                        flush=True,
                    )

                try:
                    if is_historical:
                        team_data = BasketFiAPI.get_team(str(team_id))
                    else:
                        # Pass competition_id and category_id to get historical roster data
                        team_data = BasketFiAPI.get_team(
                            str(team_id),
                            competition_id=season_competition_id,
                            category_id=category_id,
                        )
                    if "team" in team_data:
                        teams_with_details.append(team_data["team"])
                    else:
                        teams_with_details.append(team_info)
                except Exception as e:
                    if verbose:
                        print(f"        ✗ Error: {e}")
                    teams_with_details.append({**team_info, "error": str(e)})

            season_data["teams"] = teams_with_details
            total_teams_fetched += len(teams_with_details)

            if verbose:
                print()
                print(
                    f"    ✓ Fetched {len(teams_with_details)} teams for season {season_name}\n"
                )

            comprehensive_data["seasons"].append(season_data)

        except Exception as e:
            if verbose:
                print(f"    ✗ Error processing season {season_name}: {str(e)}\n")
            continue

    comprehensive_data["metadata"]["seasons_processed"] = len(
        comprehensive_data["seasons"]
    )
    comprehensive_data["metadata"]["total_matches_found"] = total_matches_found
    comprehensive_data["metadata"]["total_played_matches_saved"] = total_played_matches
    comprehensive_data["metadata"]["matches_with_advanced_stats"] = total_advanced_stats
    comprehensive_data["metadata"]["matches_failed"] = total_failed
    comprehensive_data["metadata"]["total_teams_fetched"] = total_teams_fetched

    if verbose:
        print(
            f"✓ Downloaded {total_played_matches} played matches from {len(comprehensive_data['seasons'])} seasons"
        )
        print(f"✓ Fetched {total_teams_fetched} team records across all seasons\n")

    # Save everything to a single comprehensive file
    output_file = output_path / "league_comprehensive.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(comprehensive_data, f, indent=2, ensure_ascii=False)

    # Final summary
    if verbose:
        print(f"\n{'=' * 80}")
        print("COMPREHENSIVE DOWNLOAD COMPLETE!")
        print(f"{'=' * 80}")
        print(f"League: {category_name}")
        print(f"Output file: {output_file.absolute()}")
        print("\nData summary:")
        print(f"  - Seasons: {len(comprehensive_data['seasons'])}")
        print(f"  - Matches: {total_played_matches} (from {total_matches_found} total)")
        print(f"  - Team records fetched: {total_teams_fetched}")
        if include_advanced:
            print(f"  - Matches with player data: {total_advanced_stats}")
        print(f"{'=' * 80}\n")
