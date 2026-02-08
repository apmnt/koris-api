import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx
from tqdm import tqdm

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..baskethotel_api import BasketHotelAPI
from ..genius_api import GeniusSportsAPI
from .common import (
    _augment_seasons_with_baskethotel,
    _get_baskethotel_league_id,
    _get_genius_session,
    _fetch_baskethotel_schedule_game_ids,
    _is_historical_season,
    _resolve_baskethotel_season_id,
    _stat_value,
    _summarize_baskethotel_boxscore,
    _summarize_boxscore,
    _BASKETHOTEL_DEFAULT_SEASON_ID,
)


def download_season_advanced_averages(
    category_id: str,
    season_id: str,
    output_file: str,
    all_seasons: bool = False,
    cache_file: Optional[str] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download season-level averages for rebounds, assists, and steals per game.

    Uses Genius Sports boxscores per match and aggregates totals per season.
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cache: Dict[str, Dict[str, float]] = {}
    cache_path = Path(cache_file) if cache_file else None
    if cache_path and cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    season_entries: List[Dict[str, Any]] = []
    season_ids: List[Dict[str, str]] = [{"season_id": season_id, "season_name": ""}]

    if all_seasons:
        try:
            category_data = BasketFiAPI.get_category(season_id, category_id)
            seasons = category_data.get("category", {}).get("seasons", [])
            seasons = _augment_seasons_with_baskethotel(seasons, category_id)
            season_ids = []
            for season in seasons:
                comp_id = season.get("competition_id") or season.get("season_id")
                if comp_id:
                    season_ids.append(
                        {
                            "season_id": str(comp_id),
                            "season_name": season.get("season_name") or str(comp_id),
                        }
                    )
        except Exception:
            season_ids = [{"season_id": season_id, "season_name": season_id}]
    elif not season_ids[0]["season_name"]:
        season_ids[0]["season_name"] = season_id

    for season in season_ids:
        current_season_id = season["season_id"]
        current_season_name = season.get("season_name") or current_season_id

        is_historical = _is_historical_season(
            {"season_name": current_season_name, "season_start_date": None}
        )

        match_ids: List[str] = []
        resolved_season_id = None
        league_id = None

        if is_historical:
            league_id = _get_baskethotel_league_id(category_id)
            resolved_season_id = (
                _resolve_baskethotel_season_id(current_season_name, league_id)
                or current_season_id
                or _BASKETHOTEL_DEFAULT_SEASON_ID
            )
            if verbose:
                print(
                    f"\nFetching historical matches for season {current_season_name}..."
                )
            match_ids = _fetch_baskethotel_schedule_game_ids(
                str(resolved_season_id), str(league_id), verbose
            )
        else:
            if verbose:
                print(f"\nFetching matches for season {current_season_id}...")
            matches_data = BasketFiAPI.get_matches(
                competition_id=current_season_id, category_id=category_id
            )
            matches = BasketFiParser.extract_matches(matches_data)
            processed_matches = BasketFiParser.parse_matches(
                matches, season_name=current_season_name, only_played=True
            )

            match_ids = [
                str(match.get("match_external_id"))
                for match in processed_matches
                if match.get("match_external_id")
            ]

        totals = {"rebounds": 0.0, "assists": 0.0, "steals": 0.0}
        games_with_stats = 0
        matches_to_fetch: List[str] = []

        for match_id in match_ids:
            cache_key = f"baskethotel:{match_id}" if is_historical else match_id
            if cache_key in cache:
                stats = cache[cache_key]
                totals["rebounds"] += _stat_value(stats.get("rebounds", 0))
                totals["assists"] += _stat_value(stats.get("assists", 0))
                totals["steals"] += _stat_value(stats.get("steals", 0))
                games_with_stats += 1
            else:
                matches_to_fetch.append(match_id)

        if matches_to_fetch:
            if verbose:
                print(
                    f"Fetching advanced boxscores for {len(matches_to_fetch)} matches..."
                )

            def fetch_boxscore_totals(
                match_id: str,
            ) -> tuple[str, Optional[Dict[str, float]], Optional[str]]:
                try:
                    if is_historical:
                        raise RuntimeError("Historical matches use async fetching.")
                    session = _get_genius_session(max_workers)
                    boxscore = GeniusSportsAPI.get_match_boxscore(
                        match_id,
                        session=session,
                        log_fn=tqdm.write if verbose else None,
                    )
                    return match_id, _summarize_boxscore(boxscore), None
                except Exception as exc:
                    return match_id, None, str(exc)

            if is_historical:

                async def fetch_boxscores_async(
                    match_ids: List[str],
                ) -> List[tuple[str, Optional[Dict[str, float]], Optional[str]]]:
                    client = BasketHotelAPI()
                    semaphore = asyncio.Semaphore(max_workers)
                    results: List[
                        tuple[str, Optional[Dict[str, float]], Optional[str]]
                    ] = []

                    async with httpx.AsyncClient() as session:

                        async def fetch_one(match_id: str):
                            async with semaphore:
                                try:
                                    boxscore = await client.fetch_boxscore_data_async(
                                        match_id,
                                        season_id=str(resolved_season_id),
                                        league_id=str(league_id),
                                        client=session,
                                    )
                                    return (
                                        match_id,
                                        _summarize_baskethotel_boxscore(boxscore),
                                        None,
                                    )
                                except Exception as exc:
                                    return match_id, None, str(exc)

                        tasks = [
                            asyncio.create_task(fetch_one(mid)) for mid in match_ids
                        ]

                        if verbose:
                            for coro in tqdm(
                                asyncio.as_completed(tasks),
                                total=len(tasks),
                                desc="Boxscores",
                                unit="match",
                            ):
                                results.append(await coro)
                        else:
                            results = await asyncio.gather(*tasks)

                    return results

                results = asyncio.run(fetch_boxscores_async(matches_to_fetch))
                for match_id, stats, error in results:
                    if stats:
                        cache_key = f"baskethotel:{match_id}"
                        cache[cache_key] = stats
                        totals["rebounds"] += _stat_value(stats.get("rebounds", 0))
                        totals["assists"] += _stat_value(stats.get("assists", 0))
                        totals["steals"] += _stat_value(stats.get("steals", 0))
                        games_with_stats += 1
                    elif verbose and error:
                        tqdm.write(f"  ✗ Match {match_id}: {error}")
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [
                        executor.submit(fetch_boxscore_totals, match_id)
                        for match_id in matches_to_fetch
                    ]

                    iterator = as_completed(futures)
                    if verbose:
                        iterator = tqdm(
                            iterator,
                            total=len(futures),
                            desc="Boxscores",
                            unit="match",
                        )

                    for future in iterator:
                        match_id, stats, error = future.result()
                        if stats:
                            cache_key = match_id
                            cache[cache_key] = stats
                            totals["rebounds"] += _stat_value(stats.get("rebounds", 0))
                            totals["assists"] += _stat_value(stats.get("assists", 0))
                            totals["steals"] += _stat_value(stats.get("steals", 0))
                            games_with_stats += 1
                        elif verbose and error:
                            tqdm.write(f"  ✗ Match {match_id}: {error}")

            if cache_path:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(cache, f, indent=2)

        averages = {
            "rebounds": totals["rebounds"] / games_with_stats
            if games_with_stats
            else 0.0,
            "assists": totals["assists"] / games_with_stats
            if games_with_stats
            else 0.0,
            "steals": totals["steals"] / games_with_stats if games_with_stats else 0.0,
        }

        season_entries.append(
            {
                "season_id": current_season_id,
                "season_name": current_season_name,
                "games": games_with_stats,
                "totals": totals,
                "averages": averages,
            }
        )

    output_data = {
        "metadata": {
            "category_id": category_id,
            "season_reference_id": season_id,
            "all_seasons": all_seasons,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "seasons": season_entries,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\nSaved season averages to {output_path.absolute()}")
        print("\nSeason averages per game (both teams combined):")
        header = f"{'Season':<12} {'Games':>6} {'Reb':>8} {'Ast':>8} {'Stl':>8}"
        print(header)
        print("-" * len(header))
        for season in season_entries:
            avg = season["averages"]
            print(
                f"{season['season_name']:<12} {season['games']:>6} "
                f"{avg['rebounds']:>8.2f} {avg['assists']:>8.2f} {avg['steals']:>8.2f}"
            )
