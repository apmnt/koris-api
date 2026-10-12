import json
import logging
import time
from pathlib import Path
from typing import Any

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..data import text
from ..genius_api import GeniusSportsAPI
from ..pipeline.types import Component
from .game_exports import enrich_matches, load_matches, season_reference


def download_team_season(
    team_id: str,
    category_id: str | None,
    competition_id: str,
    output_file: str,
    season_name: str | None = None,
    include_advanced: bool = False,
    include_team_stats: bool = False,
    genius_competition_id: str | None = None,
    genius_team_id: str | None = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download all matches of one team from one season.

    Optionally includes player data from advanced boxscores (if --adv-players flag is used).
    Optionally includes team season statistics (if --adv-teams flag is used).
    Player data comes from match boxscores, not separate player downloads.

    This fetches:
    - All matches for the team in the season (played matches only)
    - Team details with roster and staff
    - Advanced box scores with player stats per match (optional)
    - Team season statistics - averages, shooting, totals (optional)

    All data is saved to a single structured JSON file.

    Args:
        team_id: The team identifier
        category_id: Optional category/league identifier (e.g., "4" for Korisliiga). If not provided, will be auto-detected from team's matches.
        competition_id: The competition/season identifier (e.g., "huki2526")
        output_file: Path where output file will be saved
        season_name: Optional season name (e.g., "2024-2025") for metadata
        include_advanced: Whether to include advanced box scores with player data from Genius Sports
        include_team_stats: Whether to include team season statistics from Genius Sports
        genius_competition_id: Genius Sports competition ID (required for team stats)
        genius_team_id: Genius Sports team ID (required for team stats)
        max_workers: Number of concurrent workers for parallel downloads
        verbose: Whether to show progress output
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get team info first
    if verbose:
        print(f"\n{'=' * 80}")
        print("TEAM SEASON DATA DOWNLOAD")
        print(f"{'=' * 80}")
        print(f"Team ID: {team_id}")

    team_data = BasketFiAPI.get_team(str(team_id))
    team_name = "Unknown"
    if "team" in team_data:
        team_name = BasketFiParser.extract_team_name(team_data)

    # Auto-detect category_id if not provided
    if not category_id:
        if verbose:
            print("Category ID: Auto-detecting from team matches...")

        # Fetch team matches to determine category
        matches_data = BasketFiAPI.get_matches(team_id=team_id)
        all_matches = BasketFiParser.extract_matches(matches_data)

        # Find a match with the requested competition_id to get category_id
        for match in all_matches:
            if match.get("competition_id") == competition_id:
                category_id = text(match.get("category_id"))
                if category_id:
                    if verbose:
                        print(f"Category ID: {category_id} (auto-detected)")
                    break

        if not category_id:
            # Fallback: use the first match's category_id if available
            if all_matches and all_matches[0].get("category_id"):
                category_id = text(all_matches[0].get("category_id"))
                if verbose:
                    print(
                        f"Category ID: {category_id} (auto-detected from first match)"
                    )
            else:
                if verbose:
                    print("Warning: Could not auto-detect category_id")
                category_id = "Unknown"
    else:
        if verbose:
            print(f"Category ID: {category_id}")

    if verbose:
        print(f"Competition ID: {competition_id}")
        if season_name:
            print(f"Season: {season_name}")
        print(f"Output file: {output_path.absolute()}")
        print(f"Include advanced stats (with player data): {include_advanced}")
        print(f"{'=' * 80}\n")

    # Get category info
    if verbose:
        print("Fetching league information...")

    category_name = "Unknown"
    if category_id and category_id != "Unknown":
        try:
            category_data = BasketFiAPI.get_category(competition_id, category_id)
            if "category" in category_data:
                category_name = category_data["category"].get(
                    "category_name", "Unknown"
                )
        except Exception:
            logging.getLogger(__name__).debug(
                "team_season: operation failed", exc_info=True
            )

    if verbose:
        print(f"✓ League: {category_name}")
        print(f"✓ Team: {team_name}\n")

    # Initialize data structure
    result_data: dict[str, Any] = {
        "metadata": {
            "team_id": team_id,
            "team_name": team_name,
            "category_id": category_id,
            "category_name": category_name,
            "competition_id": competition_id,
            "season_name": season_name or competition_id,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "include_advanced_stats": include_advanced,
        },
        "team": team_data.get("team", {}),
        "matches": [],
    }

    # Fetch matches for this team
    if verbose:
        print("Downloading all matches for the team...")

    # Fetch matches for this team (API only accepts team_id OR competition_id+category_id, not both)
    matches_data = BasketFiAPI.get_matches(team_id=team_id)
    all_matches = BasketFiParser.extract_matches(matches_data)

    # Filter matches by season/competition if needed
    matches = BasketFiParser.filter_matches_by_season(
        all_matches, competition_id, category_id
    )

    if not matches:
        if verbose:
            print("No matches found.")
        result_data["metadata"]["total_matches"] = 0
        result_data["metadata"]["played_matches_saved"] = 0
    else:
        total_matches = len(matches)

        if verbose:
            print(f"Found {total_matches} matches")

        # Process matches
        processed_matches = BasketFiParser.parse_matches(
            matches, season_name=season_name or competition_id, only_played=True
        )
        if include_advanced:
            processed_matches, report = enrich_matches(
                processed_matches,
                (Component.BOXSCORE,),
                season_reference(
                    str(category_id), season_name or competition_id, historical=False
                ),
                cached=load_matches(output_path),
                cache_dir=output_path.parent / ".cache" / competition_id,
                max_workers=max_workers,
            )
            result_data["metadata"]["conversion_failures"] = report.failed
        matches_with_advanced = sum(
            bool(row.get("boxscore")) for row in processed_matches
        )
        result_data["matches"] = processed_matches
        result_data["metadata"].update(
            total_matches=total_matches,
            played_matches_saved=len(processed_matches),
            matches_with_advanced_stats=matches_with_advanced,
            matches_failed=len(processed_matches) - matches_with_advanced
            if include_advanced
            else 0,
        )

    # Fetch team statistics if requested
    if include_team_stats:
        if not genius_competition_id or not genius_team_id:
            if verbose:
                print(
                    "WARNING: --adv-teams requires --genius-competition-id and --genius-team-id"
                )
                print("  Skipping team statistics...\n")
        else:
            if verbose:
                print("Fetching team season statistics from Genius Sports...")

            try:
                team_stats = GeniusSportsAPI.get_team_statistics(
                    competition_id=genius_competition_id, team_id=genius_team_id
                )
                result_data["team_statistics"] = team_stats
                result_data["metadata"]["include_team_stats"] = True

                if verbose:
                    print(
                        f"✓ Fetched team statistics with {len(team_stats.get('averages', []))} players\n"
                    )
            except Exception as e:
                logging.getLogger(__name__).debug(
                    "team_season: operation failed", exc_info=True
                )
                if verbose:
                    print(f"✗ Error fetching team statistics: {e}\n")
                result_data["metadata"]["team_stats_error"] = str(e)

    # Save to file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    # Final summary
    if verbose:
        print(f"\n{'=' * 80}")
        print("TEAM SEASON DOWNLOAD COMPLETE!")
        print(f"{'=' * 80}")
        print(f"Team: {team_name}")
        print(f"League: {category_name}")
        print(f"Season: {season_name or competition_id}")
        print(f"Output file: {output_path.absolute()}")
        print("\nData summary:")
        print(f"  - Matches: {len(result_data['matches'])} (played matches)")
        if include_advanced:
            matches_with_player_data = sum(
                1 for m in result_data["matches"] if "boxscore" in m
            )
            print(f"  - Matches with player data: {matches_with_player_data}")
        if include_team_stats and "team_statistics" in result_data:
            team_stats = result_data["team_statistics"]
            print(f"  - Team statistics: {len(team_stats.get('averages', []))} players")
        print(f"{'=' * 80}\n")
