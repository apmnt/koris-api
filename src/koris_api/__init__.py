import argparse
import json
import requests
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, List
from tqdm import tqdm
from .basketfi_api import BasketFiAPI
from .basketfi_parser import BasketFiParser
from .baskethotel_api import BasketHotelAPI
from .genius_api import GeniusSportsAPI


def load_genius_ids(
    category_id: str, competition_id: Optional[str] = None
) -> List[str]:
    """
    Load Genius Sports competition IDs from genius_ids.json file.

    Tries multiple lookup strategies:
    1. Direct lookup by category_id and competition_id
    2. Direct lookup by category_id and season_id (if competition_id matches a season)
    3. Extract from category_external_id in API response (if available)

    Args:
        category_id: The category/league identifier (e.g., "4" for Korisliiga)
        competition_id: Optional competition/season identifier (e.g., "huki2526")

    Returns:
        List of Genius Sports competition IDs, empty list if none found
    """
    genius_ids = []

    # Try to load from genius_ids.json file
    genius_ids_path = Path(__file__).parent.parent.parent / "genius_ids.json"
    if genius_ids_path.exists():
        try:
            with open(genius_ids_path, "r", encoding="utf-8") as f:
                genius_ids_data = json.load(f)

            if category_id in genius_ids_data:
                category_data = genius_ids_data[category_id]

                # Try competition_id first
                if competition_id and competition_id in category_data:
                    ids = category_data[competition_id]
                    if isinstance(ids, list):
                        genius_ids.extend([str(id) for id in ids if id])

                # Also try to extract from category_external_id in API if available
                if not genius_ids and competition_id:
                    try:
                        category_response = BasketFiAPI.get_category(
                            competition_id, category_id
                        )
                        if "category" in category_response:
                            external_id = category_response["category"].get(
                                "category_external_id"
                            )
                            if external_id and external_id.strip():
                                genius_ids.append(external_id.strip())
                    except Exception:
                        pass  # Ignore API errors, just use file lookup

        except Exception:
            pass  # If file doesn't exist or has errors, continue

    # Remove duplicates and empty strings
    return list(dict.fromkeys([id for id in genius_ids if id]))


# Backward compatibility alias
KorisAPI = BasketFiAPI

__version__ = "0.1.0"
__all__ = [
    "BasketFiAPI",
    "KorisAPI",  # Backward compatibility
    "BasketHotelAPI",
    "GeniusSportsAPI",
    "download_season_comprehensive",
    "download_team_season",
    "download_league_comprehensive",
    "download_old_game",
    "download_old_games_bulk",
    "download_old_games_from_file",
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
    matches_to_fetch_advanced = []

    # Check if we should fetch advanced stats for matches
    if include_advanced:
        for idx, match_data in enumerate(processed_matches):
            external_id = match_data.get("match_external_id")
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
                boxscore = GeniusSportsAPI.get_match_boxscore(
                    str(match_info["external_id"])
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
    category_data = BasketFiAPI.get_category(season_id, category_id)

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
            matches_data = BasketFiAPI.get_matches(
                competition_id=season_competition_id, category_id=category_id
            )

            matches = BasketFiParser.extract_matches(matches_data)
            total_matches_found += len(matches)

            # Process matches for this season
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
                        boxscore = GeniusSportsAPI.get_match_boxscore(
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
        result = GeniusSportsAPI.get_genius_players(
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
        result = GeniusSportsAPI.get_genius_players_by_team(
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


def download_old_game(
    game_id: str,
    season_id: str = "121333",
    league_id: str = "2",
    output_file: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Download game data from BasketHotel API (for older games not available in main API).

    Args:
        game_id: BasketHotel game identifier
        season_id: Season identifier (default: 121333)
        league_id: League identifier (default: 2)
        output_file: Optional path to save the results as JSON
        verbose: Whether to show progress output

    Returns:
        Dictionary containing the game data
    """
    if verbose:
        print(f"Fetching old game data for game {game_id}...")
        print(f"  Season ID: {season_id}")
        print(f"  League ID: {league_id}")
        print(f"{'=' * 60}\n")

    try:
        client = BasketHotelAPI()
        game_data = client.fetch_game_data(game_id, season_id, league_id)

        # Generate output filename if not provided
        if output_file:
            # Save to file
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(game_data, f, indent=2, ensure_ascii=False)

            if verbose:
                print(f"\n{'=' * 60}")
                print(f"✓ Successfully saved game data to {output_path}")
                print(f"  - Game ID: {game_id}")

                # Show game info
                if game_data.get("teams", {}).get("home", {}).get("name"):
                    home_team = game_data["teams"]["home"]["name"]
                    away_team = game_data["teams"]["away"]["name"]
                    print(f"  - Teams: {home_team} vs {away_team}")

                if game_data.get("score"):
                    home_score = game_data["score"].get("home", "?")
                    away_score = game_data["score"].get("away", "?")
                    print(f"  - Score: {home_score} - {away_score}")

                if game_data.get("game_info", {}).get("date"):
                    print(f"  - Date: {game_data['game_info']['date']}")

                print(f"{'=' * 60}")

        return game_data

    except Exception as e:
        if verbose:
            print(f"Error downloading old game: {str(e)}")
        raise


def download_old_games_bulk(
    game_ids: list[str],
    season_id: str = "121333",
    league_id: str = "2",
    output_file: Optional[str] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download multiple old games from BasketHotel API in parallel.

    Args:
        game_ids: List of BasketHotel game identifiers
        season_id: Season identifier (default: 121333)
        league_id: League identifier (default: 2)
        output_file: Optional path to save the results as JSON
        max_workers: Number of concurrent workers (default: 5)
        verbose: Whether to show progress output
    """
    if verbose:
        print(f"Fetching {len(game_ids)} old games from BasketHotel API...")
        print(f"  Season ID: {season_id}")
        print(f"  League ID: {league_id}")
        print(f"  Concurrency: {max_workers} workers")
        print(f"{'=' * 60}\n")

    client = BasketHotelAPI()
    games_data = []
    games_successful = 0
    games_failed = 0

    def fetch_game(game_id: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Fetch a single game. Returns (game_data, error_msg)."""
        try:
            data = client.fetch_game_data(game_id, season_id, league_id)
            return (data, None)
        except Exception as e:
            return (None, str(e))

    # Use ThreadPoolExecutor for concurrent fetching
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(fetch_game, game_id): game_id for game_id in game_ids
        }

        # Process results with progress bar
        with tqdm(
            total=len(game_ids),
            desc="Fetching games",
            disable=not verbose,
        ) as pbar:
            for future in as_completed(futures):
                game_id = futures[future]
                game_data, error = future.result()

                if game_data:
                    # Add game ID to the data
                    game_data["baskethotel_game_id"] = game_id
                    games_data.append(game_data)
                    games_successful += 1
                else:
                    games_failed += 1
                    if verbose:
                        tqdm.write(f"  ✗ Game {game_id}: {error}")

                pbar.update(1)

    # Save to file if specified
    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        result = {
            "metadata": {
                "season_id": season_id,
                "league_id": league_id,
                "total_games_requested": len(game_ids),
                "games_successful": games_successful,
                "games_failed": games_failed,
            },
            "games": games_data,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"✓ Successfully fetched {games_successful}/{len(game_ids)} games")
        if games_failed > 0:
            print(f"  - Failed: {games_failed}")
        if output_file:
            print(f"  - Saved to: {output_file}")
        print(f"{'=' * 60}")


def download_old_games_from_file(
    input_file: str,
    season_id: str = "121333",
    league_id: str = "2",
    output_file: Optional[str] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download old games from BasketHotel API using game IDs from a file.

    The input file should contain one game ID per line, or be a JSON file with
    an array of game IDs.

    Args:
        input_file: Path to file containing game IDs
        season_id: Season identifier (default: 121333)
        league_id: League identifier (default: 2)
        output_file: Optional path to save the results as JSON
        max_workers: Number of concurrent workers (default: 5)
        verbose: Whether to show progress output
    """
    if verbose:
        print(f"Reading game IDs from {input_file}...")

    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    # Try to read as JSON first
    game_ids: list[str] = []
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                game_ids = [str(gid) for gid in data]
            elif isinstance(data, dict) and "game_ids" in data:
                game_ids = [str(gid) for gid in data["game_ids"]]
            else:
                raise ValueError(
                    "JSON file must contain an array or an object with 'game_ids' key"
                )
    except json.JSONDecodeError:
        # Not JSON, try reading as text file (one ID per line)
        with open(input_path, "r", encoding="utf-8") as f:
            game_ids = [line.strip() for line in f if line.strip()]

    if not game_ids:
        raise ValueError("No game IDs found in input file")

    if verbose:
        print(f"Found {len(game_ids)} game IDs")

    # Download the games
    download_old_games_bulk(
        game_ids=game_ids,
        season_id=season_id,
        league_id=league_id,
        output_file=output_file,
        max_workers=max_workers,
        verbose=verbose,
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
            print(f"Error: Invalid season-id ({competition_id}).")
            print("\nCommon category IDs:")
            print("  4  - Korisliiga (Men's top division)")
            print("  2  - Miesten I divisioona A (Men's 1st division A)")
            print("  13 - Naisten Korisliiga (Women's top division)")
            return

    except Exception as e:
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
        if competition_id not in valid_competition_ids:
            print(
                f"Error: Invalid season-id ({competition_id}) for category '{category_name}'."
            )
            print(f"\nAvailable seasons for category-id {category_id}:")
            for season in seasons_list:
                print(
                    f"  {season.get('competition_id', 'N/A'):15} - {season.get('season_name', 'Unknown')}"
                )
            return

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

    if not matches:
        if verbose:
            print("No matches found.")
        comprehensive_data["metadata"]["total_matches"] = 0
        comprehensive_data["metadata"]["played_matches_saved"] = 0
    else:
        total_matches = len(matches)

        if verbose:
            print(f"Found {total_matches} matches")

        # Process basic match data first
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
                            "match_date": match_data.get("match_date", "Unknown date"),
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
                    boxscore = GeniusSportsAPI.get_match_boxscore(
                        str(match_info["external_id"])
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
                1 for m in comprehensive_data["matches"] if "advanced_boxscore" in m
            )
            print(f"  - Matches with player data: {matches_with_player_data}")
        print(f"{'=' * 80}\n")


def download_team_season(
    team_id: str,
    category_id: Optional[str],
    competition_id: str,
    output_file: str,
    season_name: Optional[str] = None,
    include_advanced: bool = False,
    include_team_stats: bool = False,
    genius_competition_id: Optional[str] = None,
    genius_team_id: Optional[str] = None,
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
            print(f"Category ID: Auto-detecting from team matches...")

        # Fetch team matches to determine category
        matches_data = BasketFiAPI.get_matches(team_id=team_id)
        all_matches = BasketFiParser.extract_matches(matches_data)

        # Find a match with the requested competition_id to get category_id
        for match in all_matches:
            if match.get("competition_id") == competition_id:
                category_id = match.get("category_id")
                if category_id:
                    if verbose:
                        print(f"Category ID: {category_id} (auto-detected)")
                    break

        if not category_id:
            # Fallback: use the first match's category_id if available
            if all_matches and all_matches[0].get("category_id"):
                category_id = all_matches[0].get("category_id")
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
            pass

    if verbose:
        print(f"✓ League: {category_name}")
        print(f"✓ Team: {team_name}\n")

    # Initialize data structure
    result_data = {
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
        matches_to_fetch_advanced = []

        # Check if we should fetch advanced stats
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
                            "match_date": match_data.get("match_date", "Unknown date"),
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
                """Fetch box score for a single match."""
                try:
                    boxscore = GeniusSportsAPI.get_match_boxscore(
                        str(match_info["external_id"])
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
                    error_type = type(e).__name__
                    return (match_info["index"], None, str(e), error_type)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(fetch_boxscore, match_info): match_info
                    for match_info in matches_to_fetch_advanced
                }

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
                            if error:
                                processed_matches[index]["advanced_boxscore_error"] = {
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
                                        f"{error_type}: {error[:40]}"
                                        if error_type
                                        else error[:50]
                                    )

                                tqdm.write(
                                    f"  ✗ {match_info['match_date']} - {match_info['home_team']} vs {match_info['away_team']}: {error_display}"
                                )

                        pbar.update(1)

        result_data["matches"] = processed_matches
        result_data["metadata"]["total_matches"] = total_matches
        result_data["metadata"]["played_matches_saved"] = len(processed_matches)
        result_data["metadata"]["matches_with_advanced_stats"] = matches_with_advanced
        result_data["metadata"]["matches_failed"] = matches_failed

        if verbose:
            print(f"✓ Downloaded {len(processed_matches)} played matches\n")

    # Fetch team statistics if requested
    if include_team_stats:
        if not genius_competition_id or not genius_team_id:
            if verbose:
                print(
                    "⚠ Warning: --adv-teams requires --genius-competition-id and --genius-team-id"
                )
                print("  Skipping team statistics...\n")
        else:
            if verbose:
                print(f"Fetching team season statistics from Genius Sports...")

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
        print(f"\nData summary:")
        print(f"  - Matches: {len(result_data['matches'])} (played matches)")
        if include_advanced:
            matches_with_player_data = sum(
                1 for m in result_data["matches"] if "advanced_boxscore" in m
            )
            print(f"  - Matches with player data: {matches_with_player_data}")
        if include_team_stats and "team_statistics" in result_data:
            team_stats = result_data["team_statistics"]
            print(f"  - Team statistics: {len(team_stats.get('averages', []))} players")
        print(f"{'=' * 80}\n")


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
            # Fetch matches for this season
            matches_data = BasketFiAPI.get_matches(
                competition_id=season_competition_id, category_id=category_id
            )

            matches = BasketFiParser.extract_matches(matches_data)
            total_matches_found += len(matches)

            # Process matches for this season
            processed_matches = BasketFiParser.parse_matches(
                matches, season_name=season_name, only_played=True
            )

            matches_to_fetch_advanced = []

            # Check if we should fetch advanced stats
            if include_advanced:
                for match_idx, match_data in enumerate(processed_matches):
                    external_id = match_data.get("match_external_id")
                    if external_id:
                        matches_to_fetch_advanced.append(
                            {
                                "index": match_idx,
                                "external_id": external_id,
                                "home_team": match_data["home_team"],
                                "away_team": match_data["away_team"],
                                "match_date": match_data.get(
                                    "match_date", "Unknown date"
                                ),
                            }
                        )

            total_played_matches += len(processed_matches)

            if verbose:
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
                        boxscore = GeniusSportsAPI.get_match_boxscore(
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
                        desc=f"    Fetching advanced stats ({season_name})",
                        disable=not verbose,
                    ) as pbar:
                        for future in as_completed(futures):
                            match_info = futures[future]
                            index, boxscore, error, error_type = future.result()

                            if boxscore:
                                processed_matches[index]["advanced_boxscore"] = boxscore
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
                                        error_display = error_type
                                    else:
                                        # For other errors, show type and short message
                                        error_display = (
                                            f"{error_type}: {error[:35]}"
                                            if error_type
                                            else error[:45]
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
                        f"      [{team_idx}/{len(teams_list)}] Fetching {team_name}..."
                    )

                try:
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


def main() -> None:
    """CLI entry point for koris-api."""
    epilog = """
examples:
  # Option 1: All teams with their matches from one season
  uv run koris-api season-comprehensive --category-id 4 --season-id huki2526 --output season.json
  
  # Option 2: All matches of one team from one season
  uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output team.json

  # Option 3: All seasons with all teams and their matches
  uv run koris-api league-comprehensive --category-id 4 --output-dir korisliiga_data

  # Add --adv-players to include per-match player stats from advanced boxscores
  # Add --adv-teams to include team season statistics (averages, shooting, totals)

common category IDs:
  4  - Korisliiga (Men's top division)
  2  - Miesten I divisioona A (Men's 1st division A)
  13 - Naisten Korisliiga (Women's top division)

notes:
  All commands save data to a single structured JSON file.
  Use --adv-players for per-match player statistics from advanced boxscores.
  Use --adv-teams for team season statistics (requires Genius Sports competition ID).
  For team-season action, category-id is optional and will be auto-detected from team's matches.
"""

    parser = argparse.ArgumentParser(
        description="Access Koris API from command line",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        choices=[
            "season-comprehensive",
            "team-season",
            "league-comprehensive",
        ],
        help="Action to perform",
    )
    parser.add_argument(
        "--season-id",
        default="huki2526",
        help="Season ID (default: huki2526 for current season)",
    )
    parser.add_argument(
        "--category-id",
        help="Category ID (default: 4 for Korisliiga, optional for team-season - will be auto-detected)",
    )
    parser.add_argument(
        "--competition-id",
        help="Genius Sports competition ID (for players-season and players-team)",
    )
    parser.add_argument(
        "--team-id",
        help="Team ID (for team-season)",
    )
    parser.add_argument(
        "--genius-competition-id",
        help="Genius Sports competition ID (required for --adv-teams)",
    )
    parser.add_argument(
        "--genius-team-id",
        help="Genius Sports team ID (required for --adv-teams)",
    )
    parser.add_argument(
        "--old-season-id",
        default="121333",
        help="BasketHotel season ID for old games (default: 121333)",
    )
    parser.add_argument(
        "--old-league-id",
        default="2",
        help="BasketHotel league ID for old games (default: 2)",
    )
    parser.add_argument(
        "--output",
        help="Output file path (auto-generated if not specified)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for comprehensive downloads (for league-comprehensive)",
    )
    parser.add_argument(
        "--adv-players",
        action="store_true",
        help="Include advanced boxscores with player data from Genius Sports (per-match stats)",
    )
    parser.add_argument(
        "--adv-teams",
        action="store_true",
        help="Include team season statistics (averages, shooting, totals) from Genius Sports",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Concurrent workers for advanced stats (default: 10)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    try:
        # Provide default category_id if not specified (except for team-season which auto-detects)
        if args.action != "team-season" and not args.category_id:
            args.category_id = "4"  # Default to Korisliiga

        # Option 1: All teams with their matches from one season
        if args.action == "season-comprehensive":
            # For season-comprehensive, competition_id is required (it's the season identifier)
            if not args.season_id:
                print("Error: --season-id is required for season-comprehensive action")
                print(
                    "Example: uv run koris-api season-comprehensive --category-id 4 --season-id huki2526 --output season.json"
                )
                return

            # Generate output filename if not provided
            if not args.output:
                from datetime import datetime

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                args.output = f"season_{args.season_id}_{timestamp}.json"

            # Get season name from category data if possible
            season_name = None
            try:
                category_data = BasketFiAPI.get_category(
                    args.season_id, args.category_id
                )
                if (
                    "category" in category_data
                    and "seasons" in category_data["category"]
                ):
                    for season in category_data["category"]["seasons"]:
                        if season.get("competition_id") == args.season_id:
                            season_name = season.get("season_name")
                            break
            except Exception:
                pass  # Use competition_id as season_name if we can't get it

            download_season_comprehensive(
                category_id=args.category_id,
                competition_id=args.season_id,
                output_file=args.output,
                season_name=season_name,
                include_advanced=args.adv_players,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

        # Option 2: All matches of one team from one season
        elif args.action == "team-season":
            if not args.team_id:
                print("Error: --team-id is required for team-season action")
                print(
                    "Example: uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output team.json"
                )
                return

            if not args.season_id:
                print("Error: --season-id is required for team-season action")
                print(
                    "Example: uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output team.json"
                )
                return

            # Generate output filename if not provided
            if not args.output:
                from datetime import datetime

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                args.output = f"team_{args.team_id}_{args.season_id}_{timestamp}.json"

            # Get season name from category data if possible (and if category_id is provided)
            season_name = None
            if args.category_id:
                try:
                    category_data = BasketFiAPI.get_category(
                        args.season_id, args.category_id
                    )
                    if (
                        "category" in category_data
                        and "seasons" in category_data["category"]
                    ):
                        for season in category_data["category"]["seasons"]:
                            if season.get("competition_id") == args.season_id:
                                season_name = season.get("season_name")
                                break
                except Exception:
                    pass

            download_team_season(
                team_id=args.team_id,
                category_id=args.category_id,
                competition_id=args.season_id,
                output_file=args.output,
                season_name=season_name,
                include_advanced=args.adv_players,
                include_team_stats=args.adv_teams,
                genius_competition_id=args.genius_competition_id,
                genius_team_id=args.genius_team_id,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

        # Option 3: All seasons with all teams and their matches
        elif args.action == "league-comprehensive":
            # Validate output directory
            if not args.output_dir:
                from datetime import datetime

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                args.output_dir = f"league_{args.category_id}_{timestamp}"

            download_league_comprehensive(
                category_id=args.category_id,
                output_dir=args.output_dir,
                season_id=args.season_id,
                include_advanced=args.adv_players,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

    except Exception as e:
        print(f"Error: {e}")
