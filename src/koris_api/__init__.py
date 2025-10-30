import argparse
import json
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any
from tqdm import tqdm
from .api import KorisAPI

__version__ = "0.1.0"
__all__ = [
    "KorisAPI",
    "download_matches_with_boxscores",
    "download_league_all_seasons",
    "download_players_season",
    "download_players_by_team",
    "main",
]


def download_matches_with_boxscores(
    season_id: str,
    category_id: str,
    output_file: str,
    include_advanced: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """Download all matches for a season, optionally including advanced box scores."""

    if verbose:
        print(f"Fetching matches for season {season_id}, category {category_id}...")

    # Fetch all matches
    data = KorisAPI.get_matches(competition_id=season_id, category_id=category_id)
    matches = data.get("matches", [])

    if not matches:
        print("No matches found.")
        return

    total_matches = len(matches)
    if verbose:
        print(f"Found {total_matches} matches")

    # Process basic match data first
    processed_matches = []
    matches_to_fetch_advanced = []

    for match in matches:
        # Check if match has been played (has scores)
        home_score = match.get("fs_A")
        away_score = match.get("fs_B")
        is_played = (
            home_score is not None
            and away_score is not None
            and home_score != ""
            and away_score != ""
        )

        # Only process played matches
        if not is_played:
            continue

        match_data = {
            "match_id": match.get("match_id"),
            "match_external_id": match.get("match_external_id"),
            "date": match.get("date"),
            "time": match.get("time"),
            "home_team": match.get("club_A_name"),
            "home_team_id": match.get("team_A_id"),
            "away_team": match.get("club_B_name"),
            "away_team_id": match.get("team_B_id"),
            "home_score": home_score,
            "away_score": away_score,
            "status": match.get("status"),
            "venue": match.get("venue_name"),
            "competition": match.get("competition_name"),
            "category": match.get("category_name"),
            "season": match.get("season_id"),
        }

        processed_matches.append(match_data)

        # Check if we should fetch advanced stats for this match
        if include_advanced:
            external_id = match.get("match_external_id")
            if external_id:
                matches_to_fetch_advanced.append(
                    {
                        "index": len(processed_matches) - 1,
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
        ) -> tuple[int, Optional[Dict[str, Any]], Optional[str], Optional[str]]:
            """Fetch box score for a single match. Returns (index, boxscore_data, error_msg, error_type)."""
            try:
                boxscore = KorisAPI.get_match_boxscore(str(match_info["external_id"]))
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
                        processed_matches[index]["advanced_boxscore"] = boxscore
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
                                error_display = error_type
                            else:
                                # For other errors, show type and short message
                                error_display = (
                                    f"{error_type}: {error[:40]}"
                                    if error_type
                                    else error[:50]
                                )

                            tqdm.write(
                                f"  ✗ {match_info['home_team']} vs {match_info['away_team']}: {error_display}"
                            )

                    pbar.update(1)

    # Save to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "metadata": {
            "season_id": season_id,
            "category_id": category_id,
            "total_matches_in_season": total_matches,
            "played_matches_saved": len(processed_matches),
            "matches_with_advanced_stats": matches_with_advanced,
            "matches_failed": matches_failed,
            "include_advanced_stats": include_advanced,
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
    category_data = KorisAPI.get_category(season_id, category_id)

    if "category" not in category_data or "seasons" not in category_data["category"]:
        print("Error: Could not retrieve seasons for this category.")
        return

    seasons = category_data["category"]["seasons"]
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

        if verbose:
            print(f"[{idx}/{len(seasons)}] Processing season: {season_name}")

        try:
            # Fetch matches for this season
            matches_data = KorisAPI.get_matches(
                competition_id=season_competition_id, category_id=category_id
            )

            matches = matches_data.get("matches", [])
            total_matches_found += len(matches)

            # Process matches for this season
            processed_matches = []
            matches_to_fetch_advanced = []

            for match in matches:
                # Check if match has been played
                home_score = match.get("fs_A")
                away_score = match.get("fs_B")
                is_played = (
                    home_score is not None
                    and away_score is not None
                    and home_score != ""
                    and away_score != ""
                )

                # Only process played matches
                if not is_played:
                    continue

                match_data = {
                    "match_id": match.get("match_id"),
                    "match_external_id": match.get("match_external_id"),
                    "date": match.get("date"),
                    "time": match.get("time"),
                    "home_team": match.get("club_A_name"),
                    "home_team_id": match.get("team_A_id"),
                    "away_team": match.get("club_B_name"),
                    "away_team_id": match.get("team_B_id"),
                    "home_score": home_score,
                    "away_score": away_score,
                    "status": match.get("status"),
                    "venue": match.get("venue_name"),
                    "competition": match.get("competition_name"),
                    "category": match.get("category_name"),
                    "season": season_name,
                    "season_id": season_data_id,
                }

                processed_matches.append(match_data)

                # Check if we should fetch advanced stats
                if include_advanced:
                    external_id = match.get("match_external_id")
                    if external_id:
                        matches_to_fetch_advanced.append(
                            {
                                "index": len(all_matches) + len(processed_matches) - 1,
                                "external_id": external_id,
                                "home_team": match_data["home_team"],
                                "away_team": match_data["away_team"],
                            }
                        )

            total_played_matches += len(processed_matches)
            all_matches.extend(processed_matches)

            if verbose:
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
                        boxscore = KorisAPI.get_match_boxscore(
                            str(match_info["external_id"])
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
                                all_matches[index]["advanced_boxscore"] = boxscore
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
                    "total_matches": len(matches),
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


def download_players_season(
    competition_id: str,
    output_file: str,
    verbose: bool = True,
) -> None:
    """Download all players and their gamelogs for a specific Genius Sports competition."""

    if verbose:
        print(f"Fetching players for Genius Sports competition {competition_id}...")
        print(f"{'=' * 60}\n")

    try:
        # Use the API method to get all players
        result = KorisAPI.get_genius_players(
            competition_id=competition_id, output_file=output_file
        )

        # Summary already printed by get_genius_players
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"✓ Successfully saved player data to {output_file}")
            print(f"  - Competition ID: {competition_id}")
            print(f"  - Teams: {len(result['teams'])}")
            print(f"  - Players: {len(result['players'])}")

            # Count players with errors
            errors = sum(1 for p in result["players"] if "error" in p)
            if errors > 0:
                print(f"  - Players with errors: {errors}")

            # Count total games
            total_games = sum(len(p.get("games", [])) for p in result["players"])
            print(f"  - Total games logged: {total_games}")
            print(f"{'=' * 60}")

    except Exception as e:
        print(f"Error downloading players: {str(e)}")
        raise


def download_players_by_team(
    competition_id: str,
    team_id: str,
    output_file: str,
    verbose: bool = True,
) -> None:
    """Download players and their gamelogs for a specific team in a Genius Sports competition."""

    if verbose:
        print(f"Fetching players for team {team_id} in competition {competition_id}...")
        print(f"{'=' * 60}\n")

    try:
        # Use the API method to get players by team
        result = KorisAPI.get_genius_players_by_team(
            competition_id=competition_id, team_id=team_id, output_file=output_file
        )

        # Summary already printed by get_genius_players_by_team
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"✓ Successfully saved player data to {output_file}")
            print(f"  - Competition ID: {competition_id}")
            print(f"  - Team: {result.get('team_name', 'Unknown')} (ID: {team_id})")
            print(f"  - Players: {len(result['players'])}")

            # Count total games
            total_games = sum(len(p.get("games", [])) for p in result["players"])
            print(f"  - Total games logged: {total_games}")
            print(f"{'=' * 60}")

    except Exception as e:
        print(f"Error downloading players: {str(e)}")
        raise


def main() -> None:
    """CLI entry point for koris-api."""
    epilog = """
examples:
  # Download matches for a season 
  uv run koris-api matches-season --category-id 4 --season-id huki2526
  
  # Download all matches from entire league history
  uv run koris-api matches-league --category-id 4
  
  # Download teams for a season (TODO)
  uv run koris-api teams-season --category-id 4
  
  # Download teams for entire league (TODO)
  uv run koris-api teams-league --category-id 4
  
  # Download players for a Genius Sports competition (1. divisioona)
  uv run koris-api players-season --competition-id 42145
  
  # Download players for a specific team (much faster than full season)
  uv run koris-api players-team --competition-id 42145 --team-id 40154
  
  # Download players for entire league (TODO)
  uv run koris-api players-league --category-id 4

common category IDs:
  4  - Korisliiga (Men's top division)
  2  - Miesten I divisioona A (Men's 1st division A)
  13 - Naisten Korisliiga (Women's top division)

common Genius Sports competition IDs (for players):
  42145 - Miesten I divisioona A (2024-2025)
  39346 - Miesten I divisioona A (2023-2024)

common team IDs for competition 42145 (Miesten I divisioona A 2024-2025):
  40154 - ACO Basket
  40157 - HBA-Märsky
  40868 - Helsingin NMKY
  40151 - Jyväskylä Basketball Academy
  40873 - Karkkila
  98525 - Kipinä Basket
  98486 - Lappeenrannan NMKY
  40152 - Pyrintö Akatemia A
  40876 - Raholan Pyrkivä
  96823 - Raiders Basket
  40158 - Torpan Pojat
  40751 - Äänekosken Huima
"""

    parser = argparse.ArgumentParser(
        description="Access Koris API from command line",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        choices=[
            "matches-season",
            "matches-league",
            "teams-season",
            "teams-league",
            "players-season",
            "players-team",
            "players-league",
        ],
        help="Action: matches-season, matches-league, teams-season, teams-league, players-season, players-team, players-league",
    )
    parser.add_argument(
        "--season-id",
        default="huki2526",
        help="Season ID (default: huki2526 for current season)",
    )
    parser.add_argument(
        "--category-id",
        default="4",
        help="Category ID (default: 4 for Korisliiga)",
    )
    parser.add_argument(
        "--competition-id",
        help="Genius Sports competition ID (for players-season and players-team)",
    )
    parser.add_argument(
        "--team-id",
        help="Genius Sports team ID (for players-team)",
    )
    parser.add_argument(
        "--output",
        help="Output file path (auto-generated if not specified)",
    )
    parser.add_argument(
        "--advanced",
        action="store_true",
        help="Include advanced stats from HTML (matches only)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Concurrent workers for advanced stats (default: 5)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    try:
        # Generate output filename if not provided
        from datetime import datetime

        if not args.output:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            action_type = args.action.split("-")[0]  # matches, teams, or players
            scope = args.action.split("-")[1]  # season or league
            advanced_suffix = "_advanced" if args.advanced else ""
            args.output = f"{action_type}_{scope}_{timestamp}{advanced_suffix}.json"

        # MATCHES
        if args.action == "matches-season":
            download_matches_with_boxscores(
                season_id=args.season_id,
                category_id=args.category_id,
                output_file=args.output,
                include_advanced=args.advanced,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )
        elif args.action == "matches-league":
            download_league_all_seasons(
                category_id=args.category_id,
                output_file=args.output,
                season_id=args.season_id,
                include_advanced=args.advanced,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

        # TEAMS
        elif args.action == "teams-season":
            if args.advanced:
                print("Warning: Advanced stats for teams not yet implemented (ignored)")
            print("TODO: Implement download_teams_season function")
        elif args.action == "teams-league":
            if args.advanced:
                print("Warning: Advanced stats for teams not yet implemented (ignored)")
            print("TODO: Implement download_teams_league function")

        # PLAYERS
        elif args.action == "players-season":
            if not args.competition_id:
                print("Error: --competition-id is required for players-season action")
                print("Example: uv run koris-api players-season --competition-id 42145")
                return

            download_players_season(
                competition_id=args.competition_id,
                output_file=args.output,
                verbose=not args.quiet,
            )
        elif args.action == "players-team":
            if not args.competition_id:
                print("Error: --competition-id is required for players-team action")
                print(
                    "Example: uv run koris-api players-team --competition-id 42145 --team-id 40154"
                )
                return
            if not args.team_id:
                print("Error: --team-id is required for players-team action")
                print(
                    "Example: uv run koris-api players-team --competition-id 42145 --team-id 40154"
                )
                return

            download_players_by_team(
                competition_id=args.competition_id,
                team_id=args.team_id,
                output_file=args.output,
                verbose=not args.quiet,
            )
        elif args.action == "players-league":
            if args.advanced:
                print(
                    "Warning: Advanced stats for players not yet implemented (ignored)"
                )
            print("TODO: Implement download_players_league function")

    except Exception as e:
        print(f"Error: {e}")
