from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, Dict, Any

from ..baskethotel_api import BasketHotelAPI
from .common import (
    _fetch_baskethotel_schedule_game_ids,
    _get_baskethotel_league_id,
    _get_baskethotel_session,
    _resolve_baskethotel_season_id,
)


def download_baskethotel_season_playbyplay(
    category_id: str,
    season_id: str,
    output_file: str,
    limit_games: Optional[int] = None,
    max_workers: int = 5,
    show_header: bool = True,
    verbose: bool = True,
) -> None:
    """
    Download BasketHotel play-by-play for every game in a season.

    Args:
        category_id: League category identifier (e.g., "4" for Korisliiga)
        season_id: BasketHotel season name (e.g., "2015-2016") or season ID
        output_file: Path where output file will be saved
        max_workers: Number of concurrent workers for parallel downloads
        verbose: Whether to show progress output
    """
    # NOTE: This is a minimal helper. Prefer season-boxscores with --playbyplay.
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    league_id = _get_baskethotel_league_id(category_id)
    resolved_season_id = (
        _resolve_baskethotel_season_id(season_id, league_id) or season_id
    )

    if verbose and show_header:
        print(f"Fetching BasketHotel play-by-play for {season_id}...")
        print(f"  League ID: {league_id}")
        print(f"  Season ID: {resolved_season_id}")
        print(f"  Concurrency: {max_workers} workers")
        print(f"  Output: {output_path.absolute()}")
        print(f"{'=' * 60}\n")

    game_ids = _fetch_baskethotel_schedule_game_ids(
        str(resolved_season_id), league_id, verbose
    )
    if limit_games is not None:
        game_ids = list(game_ids)[:limit_games]

    if not game_ids:
        print("No games found in BasketHotel schedule.")
        return

    client = BasketHotelAPI()
    matches_data: list[Dict[str, Any]] = []
    errors: list[Dict[str, Any]] = []

    def fetch_game(game_id: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            session = _get_baskethotel_session()
            playbyplay = client.fetch_playbyplay_data(
                game_id=str(game_id),
                season_id=str(resolved_season_id),
                league_id=str(league_id),
                session=session,
            )
            return (playbyplay, None)
        except Exception as exc:
            return (None, str(exc))

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_game, gid): gid for gid in game_ids}
        with tqdm(
            total=len(game_ids),
            desc="Fetching BasketHotel play-by-play",
            disable=not verbose,
        ) as pbar:
            for future in as_completed(futures):
                game_id = futures[future]
                playbyplay, error = future.result()
                if playbyplay:
                    matches_data.append(
                        {
                            "match_id": str(game_id),
                            "playbyplay": playbyplay,
                        }
                    )
                else:
                    errors.append({"game_id": str(game_id), "error": error})
                    if verbose and error:
                        tqdm.write(f"  ✗ Game {game_id}: {error}")
                pbar.update(1)

    result = {
        "metadata": {
            "category_id": category_id,
            "season_id": str(resolved_season_id),
            "season_name": season_id,
            "source": "baskethotel",
            "league_id": str(league_id),
            "total_games_requested": len(game_ids),
            "matches_saved": len(matches_data),
            "matches_failed": len(errors),
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "matches": matches_data,
        "errors": errors,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose and show_header:
        print(f"\n{'=' * 60}")
        print(f"✓ Successfully fetched {len(matches_data)}/{len(game_ids)} games")
        if errors:
            print(f"  - Failed: {len(errors)}")
        print(f"  - Saved to: {output_path}")
        print(f"{'=' * 60}")
