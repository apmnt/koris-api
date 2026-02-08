import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any

from tqdm import tqdm

from ..baskethotel_api import BasketHotelAPI


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
