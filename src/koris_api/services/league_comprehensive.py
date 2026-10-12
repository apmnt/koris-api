import logging
import time
from pathlib import Path
from typing import Any

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..pipeline.export import write_json
from ..pipeline.types import Component
from ..seasons import current_season_id
from .common import (
    _fetch_historical_matches,
    _is_historical_season,
    available_season_rows,
)
from .game_exports import enrich_matches, season_reference


def download_league_comprehensive(
    category_id: str,
    output_dir: str,
    season_id: str | None = None,
    include_advanced: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> dict[str, Any]:
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
    season_id = season_id or current_season_id()
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
        logging.getLogger(__name__).debug(
            "league_comprehensive: operation failed", exc_info=True
        )
        error_msg = "Error: Failed to fetch category/season information.\n"
        error_msg += f"This usually means the category-id ({category_id}) or reference season-id ({season_id}) is invalid.\n"
        error_msg += f"Details: {e!s}\n\n"
        error_msg += "Common category IDs:\n"
        error_msg += "  4  - Korisliiga (Men's top division)\n"
        error_msg += "  2  - Miesten I divisioona A (Men's 1st division A)\n"
        error_msg += "  13 - Naisten Korisliiga (Women's top division)\n"
        raise ValueError(error_msg) from e

    if "category" not in category_data or "seasons" not in category_data["category"]:
        print(f"Error: Could not retrieve seasons for category-id ({category_id}).")
        print(
            f"This usually means the category-id or the reference season-id ({season_id}) is invalid."
        )
        print("\nCommon category IDs:")
        print("  4  - Korisliiga (Men's top division)")
        print("  2  - Miesten I divisioona A (Men's 1st division A)")
        print("  13 - Naisten Korisliiga (Women's top division)")
        raise ValueError(f"No category seasons for {category_id}")

    category = category_data["category"]
    seasons = category["seasons"]
    seasons = available_season_rows(seasons, category_id)
    category_name = category.get("category_name", "Unknown")

    # Validate category name
    if category_name == "Unknown" or not category_name:
        print("Warning: Category name could not be determined.")
        print(f"This might indicate an invalid category-id ({category_id}).")
        print("Continuing anyway, but results may be empty...")

    if not seasons:
        print(f"No seasons found for category-id ({category_id}).")
        print("This category might not have any active seasons.")
        raise ValueError(f"No seasons found for {category_id}")

    if verbose:
        print(f"✓ League: {category_name}")
        print(f"✓ Found {len(seasons)} seasons")
        print("\nAvailable seasons:")
        for season in seasons:
            print(
                f"  {season.get('competition_id', 'N/A'):15} - {season.get('season_name', 'Unknown')}"
            )
        print()

    # Initialize comprehensive data structure
    comprehensive_data: dict[str, Any] = {
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
    total_teams_failed = 0
    total_conversion_failed = 0
    season_errors: list[dict[str, str]] = []

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

            if include_advanced:
                processed_matches, report = enrich_matches(
                    processed_matches,
                    (Component.BOXSCORE,),
                    season_reference(
                        category_id, season_name, historical=is_historical
                    ),
                    max_workers=max_workers,
                    cache_dir=output_path.parent
                    / ".cache"
                    / str(season_competition_id),
                )
                season_advanced = sum(
                    bool(row.get("boxscore")) for row in processed_matches
                )
                season_failed = len(processed_matches) - season_advanced
                total_advanced_stats += season_advanced
                total_failed += season_failed
                season_data["conversion_failures"] = report.failed
                total_conversion_failed += report.failed

            # Store matches for this season
            season_data["matches"] = processed_matches
            total_played_matches += len(processed_matches)

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
                    logging.getLogger(__name__).debug(
                        "league_comprehensive: operation failed", exc_info=True
                    )
                    if verbose:
                        print(f"        ✗ Error: {e}")
                    teams_with_details.append({**team_info, "error": str(e)})
                    total_teams_failed += 1

            season_data["teams"] = teams_with_details
            total_teams_fetched += len(teams_with_details)

            if verbose:
                print()
                print(
                    f"    ✓ Fetched {len(teams_with_details)} teams for season {season_name}\n"
                )

            comprehensive_data["seasons"].append(season_data)

        except Exception as e:
            logging.getLogger(__name__).debug(
                "league_comprehensive: operation failed", exc_info=True
            )
            if verbose:
                print(f"    ✗ Error processing season {season_name}: {e!s}\n")
            season_errors.append({"season_id": str(season_data_id), "error": str(e)})
            continue

    comprehensive_data["metadata"]["seasons_processed"] = len(
        comprehensive_data["seasons"]
    )
    comprehensive_data["metadata"]["total_matches_found"] = total_matches_found
    comprehensive_data["metadata"]["total_played_matches_saved"] = total_played_matches
    comprehensive_data["metadata"]["matches_with_advanced_stats"] = total_advanced_stats
    comprehensive_data["metadata"]["matches_failed"] = total_failed
    comprehensive_data["metadata"]["total_teams_fetched"] = total_teams_fetched
    comprehensive_data["metadata"]["teams_failed"] = total_teams_failed
    comprehensive_data["metadata"]["conversion_failures"] = total_conversion_failed
    comprehensive_data["metadata"]["seasons_failed"] = len(season_errors)
    comprehensive_data["errors"] = season_errors

    if verbose:
        print(
            f"✓ Downloaded {total_played_matches} played matches from {len(comprehensive_data['seasons'])} seasons"
        )
        print(f"✓ Fetched {total_teams_fetched} team records across all seasons\n")

    # Save everything to a single comprehensive file
    output_file = output_path / "league_comprehensive.json"

    write_json(output_file, comprehensive_data)

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

    return comprehensive_data
