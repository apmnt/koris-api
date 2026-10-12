import json
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
    _extract_season_start_year,
    _fetch_historical_matches,
    _is_historical_season,
)
from .game_exports import enrich_matches, load_matches, season_reference


def download_season_comprehensive(
    category_id: str,
    competition_id: str,
    output_file: str,
    season_name: str | None = None,
    include_advanced: bool = False,
    max_workers: int = 5,
    show_header: bool = True,
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

    if verbose and show_header:
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
                fallback_data = BasketFiAPI.get_category(
                    current_season_id(), category_id
                )
                if (
                    "category" in fallback_data
                    and "seasons" in fallback_data["category"]
                ):
                    category_name = fallback_data["category"].get(
                        "category_name", "Unknown"
                    )
                    seasons_list = fallback_data["category"].get("seasons", [])
                    season_year = _extract_season_start_year(competition_id)
                    if season_year is not None and season_year <= 2023:
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
                        raise ValueError(f"Invalid season {competition_id}")
            except Exception:
                logging.getLogger(__name__).debug(
                    "season_comprehensive: operation failed", exc_info=True
                )

            # Fallback failed, show generic error
            if "category" not in category_data:
                print(f"Error: Invalid season-id ({competition_id}).")
                print("\nCommon category IDs:")
                print("  4  - Korisliiga (Men's top division)")
                print("  2  - Miesten I divisioona A (Men's 1st division A)")
                print("  13 - Naisten Korisliiga (Women's top division)")
                raise ValueError(f"Invalid season {competition_id}")

    except Exception as e:
        logging.getLogger(__name__).debug(
            "season_comprehensive: operation failed", exc_info=True
        )
        season_year = _extract_season_start_year(competition_id)
        if season_year is not None and season_year <= 2023:
            fallback_data = BasketFiAPI.get_category(current_season_id(), category_id)
            category_data = fallback_data
        else:
            error_msg = "Error: Failed to fetch category/season information.\n"
            error_msg += f"This usually means the category-id ({category_id}) or season-id ({competition_id}) is invalid.\n"
            error_msg += f"Details: {e!s}\n\n"
            error_msg += "Common category IDs:\n"
            error_msg += "  4  - Korisliiga (Men's top division)\n"
            error_msg += "  2  - Miesten I divisioona A (Men's 1st division A)\n"
            error_msg += "  13 - Naisten Korisliiga (Women's top division)\n"
            print(error_msg)
            raise ValueError(f"Invalid category/season: {category_id}/{competition_id}")

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
        raise ValueError(f"Invalid category/season: {category_id}/{competition_id}")

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
            season_year is None or season_year > 2023
        ):
            print(
                f"Error: Invalid season-id ({competition_id}) for category '{category_name}'."
            )
            print(f"\nAvailable seasons for category-id {category_id}:")
            for season in seasons_list:
                print(
                    f"  {season.get('competition_id', 'N/A'):15} - {season.get('season_name', 'Unknown')}"
                )
            raise ValueError(f"Invalid category/season: {category_id}/{competition_id}")

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
        is_historical = season_year is not None and season_year <= 2023

    # Additional validation - check if category name is meaningful
    if category_name == "Unknown" or not category_name:
        print("Warning: Category name could not be determined.")
        print(
            f"This might indicate an invalid category-id ({category_id}) or season-id ({competition_id})."
        )
        print("Continuing anyway, but results may be empty...")

    if verbose:
        print(f"✓ League: {category_name}\n")

    # Initialize comprehensive data structure
    comprehensive_data: dict[str, Any] = {
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
    total_matches = len(matches)
    teams_with_details: list[dict[str, Any]] = []

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
    existing_teams: list[dict[str, Any]] = []
    if output_path.exists():
        try:
            existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
            existing_teams = existing_payload.get("teams") or []
        except (ValueError, OSError, AttributeError):
            existing_teams = []
    if include_advanced:
        processed_matches, report = enrich_matches(
            processed_matches,
            (Component.BOXSCORE,),
            season_reference(
                category_id, season_name or competition_id, historical=is_historical
            ),
            cached=load_matches(output_path),
            cache_dir=output_path.parent / ".cache" / competition_id,
            max_workers=max_workers,
        )
        comprehensive_data["metadata"]["conversion_failures"] = report.failed
    matches_with_advanced = sum(bool(row.get("boxscore")) for row in processed_matches)
    matches_failed = (
        len(processed_matches) - matches_with_advanced if include_advanced else 0
    )
    comprehensive_data["matches"] = processed_matches
    comprehensive_data["metadata"].update(
        total_matches=total_matches,
        played_matches_saved=len(processed_matches),
        matches_with_advanced_stats=matches_with_advanced,
        matches_failed=matches_failed,
    )

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
    if existing_teams:
        comprehensive_data["teams"] = existing_teams
        comprehensive_data["metadata"]["total_teams"] = len(existing_teams)
        if verbose:
            print("Step 3: Fetching detailed team data (rosters, officials, etc.)...")
            print(f"✓ Reused {len(existing_teams)} teams from existing file\n")
    else:
        if verbose:
            print("Step 3: Fetching detailed team data (rosters, officials, etc.)...")

        teams_with_details = []

        for idx, team_info in enumerate(teams_list, 1):
            team_id = team_info["team_id"]
            team_name = team_info["team_name"]

            if verbose:
                print(
                    f"\r\033[2K  [{idx}/{len(teams_list)}] Fetching {team_name}...",
                    end="",
                    flush=True,
                )

            try:
                team_data = BasketFiAPI.get_team(str(team_id))
                if "team" in team_data:
                    teams_with_details.append(team_data["team"])
                else:
                    teams_with_details.append(team_info)
            except Exception as e:
                logging.getLogger(__name__).debug(
                    "season_comprehensive: operation failed", exc_info=True
                )
                if verbose:
                    print(f"    ✗ Error: {e}")
                teams_with_details.append({**team_info, "error": str(e)})

        comprehensive_data["teams"] = teams_with_details
        comprehensive_data["metadata"]["total_teams"] = len(teams_with_details)

        if verbose:
            print()
            print(f"✓ Fetched {len(teams_with_details)} teams\n")

    # Save everything to a single comprehensive file
    comprehensive_data["metadata"]["teams_failed"] = sum(
        bool(team.get("error")) for team in comprehensive_data["teams"]
    )
    write_json(output_path, comprehensive_data)

    # Final summary
    if verbose and show_header:
        print(f"\n{'=' * 80}")
        print("COMPREHENSIVE SEASON DOWNLOAD COMPLETE!")
        print(f"{'=' * 80}")
        print(f"League: {category_name}")
        print(f"Season: {season_name or competition_id}")
        print(f"Output file: {output_path.absolute()}")
        print("\nData summary:")
        print(f"  - Matches: {len(comprehensive_data['matches'])} (played matches)")
        print(f"  - Teams: {len(comprehensive_data['teams'])}")
        if include_advanced:
            matches_with_player_data = sum(
                1 for m in comprehensive_data["matches"] if "boxscore" in m
            )
            print(f"  - Matches with player data: {matches_with_player_data}")
        print(f"{'=' * 80}\n")
