import argparse
import json
import requests
import time
import re
import asyncio
import httpx
import curses
import sys
from datetime import datetime
from urllib.parse import urlencode
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from typing import Optional, Dict, Any, List
from tqdm import tqdm
from .basketfi_api import BasketFiAPI
from .basketfi_parser import BasketFiParser
from .baskethotel_api import BasketHotelAPI
from .baskethotel_parser import BasketHotelParser
from .genius_api import GeniusSportsAPI
from .boxscore_normalizer import normalize_boxscore


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


_BASKETHOTEL_DEFAULT_SEASON_ID = "121333"
_BASKETHOTEL_DEFAULT_LEAGUE_ID = "2"
_BASKETHOTEL_LEAGUE_ID_BY_CATEGORY = {
    "4": "2",  # Korisliiga
}
_BASKETHOTEL_SEASON_CACHE: Dict[str, Dict[str, str]] = {}
_BASKETHOTEL_TEAM_CACHE: Dict[str, Dict[str, str]] = {}
_BASKETHOTEL_MIN_YEAR = 2010
_BASKETHOTEL_SESSION_LOCAL = threading.local()
_GENIUS_SESSION_LOCAL = threading.local()


def _get_baskethotel_league_id(category_id: str) -> str:
    return _BASKETHOTEL_LEAGUE_ID_BY_CATEGORY.get(
        str(category_id), _BASKETHOTEL_DEFAULT_LEAGUE_ID
    )


def _get_baskethotel_session() -> requests.Session:
    session = getattr(_BASKETHOTEL_SESSION_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=64, pool_maxsize=64
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _BASKETHOTEL_SESSION_LOCAL.session = session
    return session


def _get_genius_session(pool_size: int = 64) -> requests.Session:
    session = getattr(_GENIUS_SESSION_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,fi;q=0.8",
            }
        )
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_size, pool_maxsize=pool_size
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _GENIUS_SESSION_LOCAL.session = session
    return session


def _extract_season_start_year(season_name: Optional[str]) -> Optional[int]:
    if not season_name:
        return None
    if "-" in season_name:
        try:
            return int(season_name.split("-", 1)[0])
        except ValueError:
            return None
    return None


def _is_historical_season(season: Dict[str, Any]) -> bool:
    season_year = _extract_season_start_year(season.get("season_name"))
    if season_year is not None:
        return season_year < 2022
    season_start_date = season.get("season_start_date")
    if season_start_date:
        try:
            return datetime.strptime(season_start_date, "%Y-%m-%d").year < 2022
        except ValueError:
            return False
    return False


def _parse_baskethotel_date(date_text: Optional[str]) -> Optional[str]:
    if not date_text:
        return None
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_baskethotel_time(time_text: Optional[str]) -> Optional[str]:
    if not time_text:
        return None
    cleaned = time_text.strip()
    if len(cleaned) == 5 and cleaned[2] == ":":
        return f"{cleaned}:00"
    if len(cleaned) == 8 and cleaned[2] == ":" and cleaned[5] == ":":
        return cleaned
    return None


def _fetch_baskethotel_seasons(league_id: str) -> Dict[str, str]:
    if league_id in _BASKETHOTEL_SEASON_CACHE:
        return _BASKETHOTEL_SEASON_CACHE[league_id]

    params = {
        "api": "b9680714b4026e011e13a43ccb7dfa201932958c",
        "lang": "fi",
        "nnav": "1",
        "nav_object": "0",
        "hide_full_birth_date": "1",
        "flash": "0",
        "request[0][container]": "view4",
        "request[0][widget]": "320",  # season selector
        "request[0][param][league_id]": league_id,
        "request[0][param][template]": "v1",
    }
    url = f"https://widgets.baskethotel.com/widget-service/show?{urlencode(params)}"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    html = BasketHotelParser.extract_html_from_response(response.text)

    season_map: Dict[str, str] = {}
    for match in re.findall(r'value="(\d+)"[^>]*>([^<]+)<', html):
        season_id, season_name = match
        season_map[season_name.strip()] = season_id

    _BASKETHOTEL_SEASON_CACHE[league_id] = season_map
    return season_map


def _resolve_baskethotel_season_id(
    season_name: Optional[str], league_id: str
) -> Optional[str]:
    if not season_name:
        return None
    season_map = _fetch_baskethotel_seasons(league_id)
    return season_map.get(season_name)


def _augment_seasons_with_baskethotel(
    seasons: List[Dict[str, Any]], category_id: str
) -> List[Dict[str, Any]]:
    league_id = _get_baskethotel_league_id(category_id)
    if not league_id:
        return seasons

    season_map = _fetch_baskethotel_seasons(league_id)
    existing_names = {s.get("season_name") for s in seasons if s.get("season_name")}
    for season_name, season_id in season_map.items():
        season_year = _extract_season_start_year(season_name)
        if season_year is None or season_year < _BASKETHOTEL_MIN_YEAR:
            continue
        if season_name in existing_names:
            continue
        seasons.append(
            {
                "season_id": season_id,
                "season_name": season_name,
                "competition_id": season_name,
            }
        )

    seasons.sort(
        key=lambda season: _extract_season_start_year(season.get("season_name")) or 0,
        reverse=True,
    )
    return seasons


def _fetch_baskethotel_schedule_page_html(
    season_id: str, league_id: str, page: int
) -> str:
    params = {
        "api": "b9680714b4026e011e13a43ccb7dfa201932958c",
        "lang": "fi",
        "nnav": "1",
        "nav_object": "0",
        "hide_full_birth_date": "1",
        "flash": "0",
        "request[0][container]": "view4",
        "request[0][widget]": "303",  # schedule long
        "request[0][part]": "schedule_and_results",
        "request[0][param][season_id]": season_id,
        "request[0][param][league_id]": league_id,
        "request[0][param][template]": "v1",
        "request[0][param][page]": str(page),
    }
    url = f"https://widgets.baskethotel.com/widget-service/show?{urlencode(params)}"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    return BasketHotelParser.extract_html_from_response(response.text)


def _fetch_baskethotel_team_map(season_id: str, league_id: str) -> Dict[str, str]:
    cache_key = f"{league_id}:{season_id}"
    if cache_key in _BASKETHOTEL_TEAM_CACHE:
        return _BASKETHOTEL_TEAM_CACHE[cache_key]

    params = {
        "api": "b9680714b4026e011e13a43ccb7dfa201932958c",
        "lang": "fi",
        "nnav": "1",
        "nav_object": "0",
        "hide_full_birth_date": "1",
        "flash": "0",
        "request[0][container]": "view4",
        "request[0][widget]": "303",
        "request[0][param][season_id]": season_id,
        "request[0][param][league_id]": league_id,
        "request[0][param][template]": "v2",
    }
    url = f"https://widgets.baskethotel.com/widget-service/show?{urlencode(params)}"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    html = BasketHotelParser.extract_html_from_response(response.text)

    team_map: Dict[str, str] = {}
    select_start = html.find('id="2-303-filter-team"')
    if select_start != -1:
        segment = html[select_start : select_start + 5000]
        options = re.findall(
            r"<option value=\"(\d+)\"[^>]*>\s*([^<]+)\s*</option>",
            segment,
        )
        for team_id, team_name in options:
            cleaned_name = team_name.strip()
            if cleaned_name:
                team_map[cleaned_name] = team_id

    _BASKETHOTEL_TEAM_CACHE[cache_key] = team_map
    return team_map


def _extract_baskethotel_game_ids(html: str) -> List[str]:
    return re.findall(r"schedule-line-container-(\d+)", html)


def _extract_baskethotel_schedule_page_count(html: str) -> int:
    pages = [int(p) for p in re.findall(r'id="2-303-page-(\d+)"', html)]
    return max(pages) if pages else 1


def _fetch_baskethotel_schedule_game_ids(
    season_id: str, league_id: str, verbose: bool
) -> List[str]:
    first_html = _fetch_baskethotel_schedule_page_html(season_id, league_id, page=1)
    game_ids = set(_extract_baskethotel_game_ids(first_html))
    total_pages = _extract_baskethotel_schedule_page_count(first_html)

    for page in range(2, total_pages + 1):
        page_html = _fetch_baskethotel_schedule_page_html(season_id, league_id, page)
        game_ids.update(_extract_baskethotel_game_ids(page_html))

    if verbose:
        print(f"    ✓ Found {len(game_ids)} games in BasketHotel schedule")

    return sorted(game_ids)


def _merge_baskethotel_match(
    base_match: Dict[str, Any], game_data: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(base_match)
    game_info = game_data.get("game_info", {})
    date_value = _parse_baskethotel_date(game_info.get("date"))
    time_value = _parse_baskethotel_time(game_info.get("time"))
    if date_value:
        merged["date"] = date_value
    if time_value:
        merged["time"] = time_value
    venue_value = game_info.get("venue")
    if venue_value:
        merged["venue"] = venue_value

    score = game_data.get("score", {})
    if "home" in score and score["home"] is not None:
        merged["home_score"] = str(score["home"])
    if "away" in score and score["away"] is not None:
        merged["away_score"] = str(score["away"])
    if merged.get("home_score") and merged.get("away_score"):
        merged["status"] = "Played"

    teams = game_data.get("teams", {})
    home_name = teams.get("home", {}).get("name")
    away_name = teams.get("away", {}).get("name")
    if home_name:
        merged["home_team"] = home_name
    if away_name:
        merged["away_team"] = away_name

    return merged


def _fetch_historical_matches(
    matches: List[Dict[str, Any]],
    season_name: str,
    season_id: Optional[str],
    category_id: str,
    category_name: Optional[str],
    max_workers: int,
    verbose: bool,
) -> List[Dict[str, Any]]:
    client = BasketHotelAPI()
    league_id = _get_baskethotel_league_id(category_id)
    resolved_season_id = (
        _resolve_baskethotel_season_id(season_name, league_id)
        or season_id
        or _BASKETHOTEL_DEFAULT_SEASON_ID
    )
    team_map = _fetch_baskethotel_team_map(resolved_season_id, league_id)

    game_ids = _fetch_baskethotel_schedule_game_ids(
        resolved_season_id, league_id, verbose
    )
    if not game_ids:
        return []

    def fetch_game(game_id: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            session = _get_baskethotel_session()
            game_data = client.fetch_game_data(
                str(game_id),
                season_id=resolved_season_id,
                league_id=league_id,
                session=session,
            )
            base_match = {
                "match_id": str(game_id),
                "match_external_id": None,
                "date": None,
                "time": None,
                "home_team": None,
                "home_team_id": None,
                "away_team": None,
                "away_team_id": None,
                "home_score": None,
                "away_score": None,
                "status": None,
                "venue": None,
                "competition": None,
                "category": category_name,
                "season": season_name,
            }
            merged = _merge_baskethotel_match(base_match, game_data)
            merged["competition"] = merged.get("competition") or season_name
            merged["category"] = merged.get("category") or category_name
            home_name = merged.get("home_team")
            away_name = merged.get("away_team")
            if home_name and not merged.get("home_team_id"):
                merged["home_team_id"] = team_map.get(home_name)
            if away_name and not merged.get("away_team_id"):
                merged["away_team_id"] = team_map.get(away_name)
            return (merged, None)
        except Exception as exc:
            return (None, str(exc))

    processed_matches: list[Dict[str, Any]] = []
    games_failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_game, game_id): game_id for game_id in game_ids}

        with tqdm(
            total=len(matches),
            desc=f"    Fetching historical games ({season_name})",
            disable=not verbose,
        ) as pbar:
            for future in as_completed(futures):
                merged, error = future.result()
                if merged and merged.get("home_score") and merged.get("away_score"):
                    processed_matches.append(merged)
                else:
                    games_failed += 1
                    if verbose and error:
                        game_id = futures[future]
                        tqdm.write(f"      ✗ Game {game_id}: {error}")
                pbar.update(1)

    if verbose:
        print(
            f"    ✓ Historical games fetched: {len(processed_matches)} played, {games_failed} failed"
        )

    return processed_matches


def _stat_value(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stat_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clean_player_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^#?\d+", "", cleaned).strip()
    return cleaned


def _extract_team_stat(team: Dict[str, Any], stat_name: str) -> float:
    totals = team.get("totals", {})
    total_value = totals.get(stat_name)
    if total_value not in (None, ""):
        return _stat_value(total_value)

    total = 0.0
    for player in team.get("players", []):
        total += _stat_value(player.get(stat_name, 0))
    return total


def _summarize_boxscore(boxscore: Dict[str, Any]) -> Dict[str, float]:
    totals = {"rebounds": 0.0, "assists": 0.0, "steals": 0.0}
    for team in boxscore.get("teams", []):
        totals["rebounds"] += _extract_team_stat(team, "Total Rebounds")
        totals["assists"] += _extract_team_stat(team, "Assists")
        totals["steals"] += _extract_team_stat(team, "Steals")
    return totals


def _summarize_baskethotel_boxscore(boxscore: Dict[str, Any]) -> Dict[str, float]:
    totals = {"rebounds": 0.0, "assists": 0.0, "steals": 0.0}
    for team in boxscore.get("teams", []):
        totals["rebounds"] += _stat_value(team.get("rebounds", 0))
        totals["assists"] += _stat_value(team.get("assists", 0))
        totals["steals"] += _stat_value(team.get("steals", 0))
    return totals


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
                    results: List[tuple[str, Optional[Dict[str, float]], Optional[str]]] = []

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

                        tasks = [asyncio.create_task(fetch_one(mid)) for mid in match_ids]

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
            "assists": totals["assists"] / games_with_stats if games_with_stats else 0.0,
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


def download_season_game_leaders(
    category_id: str,
    season_id: str,
    output_file: str,
    max_workers: int = 10,
    verbose: bool = True,
) -> None:
    """
    Download per-game player leaders (points, rebounds, assists, steals) for a season.

    Uses BasketHotel for historical seasons (pre-2022).
    """
    season_name = season_id
    if not _is_historical_season({"season_name": season_name}):
        raise ValueError(
            "season-game-leaders currently supports historical seasons (pre-2022) only."
        )

    league_id = _get_baskethotel_league_id(category_id)
    resolved_season_id = (
        _resolve_baskethotel_season_id(season_name, league_id)
        or season_id
        or _BASKETHOTEL_DEFAULT_SEASON_ID
    )

    game_ids = _fetch_baskethotel_schedule_game_ids(
        str(resolved_season_id), str(league_id), verbose
    )
    if not game_ids:
        raise ValueError("No game IDs found for this season.")

    client = BasketHotelAPI()

    async def fetch_one(match_id: str, session: httpx.AsyncClient):
        try:
            boxscore = await client.fetch_boxscore_data_async(
                match_id,
                season_id=str(resolved_season_id),
                league_id=str(league_id),
                client=session,
            )
        except Exception as exc:
            return {"game_id": match_id, "error": str(exc)}

        game_info = boxscore.get("game_info", {})
        game_teams = boxscore.get("game_teams", {})
        home_team = game_teams.get("home", {}).get("name")
        away_team = game_teams.get("away", {}).get("name")
        game_date = _parse_baskethotel_date(game_info.get("date"))
        game_label = None
        if home_team and away_team:
            game_label = f"{home_team} vs {away_team}"

        normalized = normalize_boxscore(boxscore, source="baskethotel")
        leaders = {
            "points": {"value": -1},
            "rebounds": {"value": -1},
            "assists": {"value": -1},
            "steals": {"value": -1},
        }

        for team in normalized.get("teams", []):
            team_name = team.get("team_name")
            for player in team.get("players", []) or []:
                name = _clean_player_name(player.get("player") or "")
                if not name:
                    continue
                points = _stat_int(player.get("points"))
                rebounds = _stat_int(player.get("rebounds_total"))
                assists = _stat_int(player.get("assists"))
                steals = _stat_int(player.get("steals"))

                if points > leaders["points"]["value"]:
                    leaders["points"] = {
                        "player": name,
                        "team": team_name,
                        "value": points,
                    }
                if rebounds > leaders["rebounds"]["value"]:
                    leaders["rebounds"] = {
                        "player": name,
                        "team": team_name,
                        "value": rebounds,
                    }
                if assists > leaders["assists"]["value"]:
                    leaders["assists"] = {
                        "player": name,
                        "team": team_name,
                        "value": assists,
                    }
                if steals > leaders["steals"]["value"]:
                    leaders["steals"] = {
                        "player": name,
                        "team": team_name,
                        "value": steals,
                    }

        return {
            "game_id": match_id,
            "game_date": game_date,
            "home_team": home_team,
            "away_team": away_team,
            "game": game_label,
            "leaders": leaders,
        }

    async def run_all():
        limits = httpx.Limits(max_connections=max_workers, max_keepalive_connections=24)
        async with httpx.AsyncClient(limits=limits, timeout=20.0) as session:
            sem = asyncio.Semaphore(max_workers)

            async def guarded(mid: str):
                async with sem:
                    return await fetch_one(mid, session)

            tasks = [asyncio.create_task(guarded(mid)) for mid in game_ids]
            results = []
            iterator = asyncio.as_completed(tasks)
            if verbose:
                iterator = tqdm(
                    iterator, total=len(tasks), desc="Boxscores", unit="match"
                )
            for coro in iterator:
                results.append(await coro)
            return results

    results = asyncio.run(run_all())

    output = {
        "metadata": {
            "season_name": season_name,
            "season_id": str(resolved_season_id),
            "league_id": str(league_id),
            "games": len(game_ids),
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "games": sorted(results, key=lambda x: int(x["game_id"])),
    }

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\nSaved season leaders to {output_path.absolute()}")


# Backward compatibility alias
KorisAPI = BasketFiAPI

__version__ = "0.1.0"
__all__ = [
    "BasketFiAPI",
    "KorisAPI",  # Backward compatibility
    "BasketHotelAPI",
    "GeniusSportsAPI",
    "download_season_comprehensive",
    "download_baskethotel_season_boxscores",
    "download_matches_with_boxscores",
    "download_team_season",
    "download_league_comprehensive",
    "download_league_boxscores_all_seasons",
    "download_season_advanced_averages",
    "download_season_game_leaders",
    "download_old_game",
    "download_old_games_bulk",
    "download_old_games_from_file",
    "retry_advanced_boxscores_404s",
    "main",
]


def download_matches_with_boxscores(
    season_id: str,
    category_id: str,
    output_file: str,
    include_advanced: bool = False,
    limit_games: Optional[int] = None,
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
    if limit_games is not None and include_advanced:
        candidate_limit = max(limit_games * 30, limit_games)
        processed_matches = processed_matches[:candidate_limit]
    elif limit_games is not None:
        processed_matches = processed_matches[:limit_games]
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

    if limit_games is not None and include_advanced:
        def has_player_stats(match: Dict[str, Any]) -> bool:
            boxscore = match.get("boxscore") or {}
            teams = boxscore.get("teams") or []
            return any(team.get("players") for team in teams)

        preferred = [m for m in processed_matches if has_player_stats(m)]
        if len(preferred) >= limit_games:
            processed_matches = preferred[:limit_games]
        else:
            preferred_ids = {m.get("match_id") for m in preferred}
            remainder = [
                m for m in processed_matches if m.get("match_id") not in preferred_ids
            ]
            processed_matches = (preferred + remainder)[:limit_games]

    matches_with_advanced = sum(1 for match in processed_matches if "boxscore" in match)
    matches_failed = sum(
        1 for match in processed_matches if "advanced_boxscore_error" in match
    )

    # Save to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "metadata": {
            "category_id": category_id,
            "season_id": season_id,
            "season_name": season_id,
            "source": "genius",
            "league_id": None,
            "total_matches_in_season": total_matches,
            "total_games_requested": None,
            "played_matches_saved": len(processed_matches),
            "matches_with_boxscore": matches_with_advanced,
            "matches_failed": matches_failed,
            "include_advanced_stats": include_advanced,
            "limit_games": limit_games,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
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


def _is_retryable_404_error(error: Dict[str, Any]) -> bool:
    error_type = str(error.get("error_type", ""))
    error_message = str(error.get("error_message", ""))
    return "404" in error_type or "404" in error_message


def retry_advanced_boxscores_404s(
    input_file: str,
    output_file: Optional[str] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """Retry advanced boxscores that previously failed with HTTP 404."""
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}")
        return

    data = json.loads(input_path.read_text(encoding="utf-8"))
    matches = data.get("matches") or []
    if not isinstance(matches, list) or not matches:
        print("No matches found in input file.")
        return

    matches_to_retry = []
    for idx, match in enumerate(matches):
        if match.get("boxscore"):
            continue
        error = match.get("advanced_boxscore_error")
        external_id = match.get("match_external_id")
        if not error or not external_id:
            continue
        if _is_retryable_404_error(error):
            matches_to_retry.append(
                {
                    "index": idx,
                    "external_id": external_id,
                    "home_team": match.get("home_team"),
                    "away_team": match.get("away_team"),
                }
            )

    if not matches_to_retry:
        print("No 404 advanced boxscore failures to retry.")
        return

    if output_file:
        output_path = Path(output_file)
    else:
        output_path = input_path.with_name(f"{input_path.stem}_retry{input_path.suffix}")

    if verbose:
        print(
            f"Retrying {len(matches_to_retry)} advanced boxscores from {input_path}..."
        )

    retried_success = 0
    retried_failed = 0

    def fetch_boxscore(
        match_info: Dict[str, Any],
    ) -> tuple[int, Optional[Dict[str, Any]], Optional[str], Optional[str]]:
        try:
            session = _get_genius_session(max_workers)
            boxscore = GeniusSportsAPI.get_match_boxscore(
                str(match_info["external_id"]),
                session=session,
                log_fn=tqdm.write if verbose else None,
            )
            return (match_info["index"], boxscore, None, None)
        except requests.exceptions.HTTPError as e:
            error_type = f"HTTP {e.response.status_code}" if e.response else "HTTP Error"
            return (match_info["index"], None, str(e), error_type)
        except ValueError as e:
            return (match_info["index"], None, str(e), "Parse Error")
        except Exception as e:
            return (match_info["index"], None, str(e), type(e).__name__)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_boxscore, match_info): match_info
            for match_info in matches_to_retry
        }

        with tqdm(
            total=len(matches_to_retry),
            desc="Retrying advanced stats",
            disable=not verbose,
        ) as pbar:
            for future in as_completed(futures):
                match_info = futures[future]
                index, boxscore, error, error_type = future.result()
                if boxscore:
                    matches[index]["boxscore"] = normalize_boxscore(
                        boxscore, source="genius"
                    )
                    matches[index].pop("advanced_boxscore_error", None)
                    retried_success += 1
                else:
                    retried_failed += 1
                    if error:
                        matches[index]["advanced_boxscore_error"] = {
                            "error_type": error_type or "Unknown",
                            "error_message": error,
                        }
                    if verbose and error:
                        if error_type == "Parse Error":
                            error_display = "Data parsing failed (check match data quality)"
                        elif error_type and error_type.startswith("HTTP"):
                            error_display = error_type
                        else:
                            error_display = (
                                f"{error_type}: {error[:40]}"
                                if error_type
                                else error[:50]
                            )
                        tqdm.write(
                            f"  ✗ {match_info.get('home_team')} vs "
                            f"{match_info.get('away_team')}: {error_display}"
                        )
                pbar.update(1)

    matches_with_advanced = sum(1 for match in matches if "boxscore" in match)
    matches_failed = sum(1 for match in matches if "advanced_boxscore_error" in match)

    metadata = data.get("metadata", {})
    metadata["matches_with_boxscore"] = matches_with_advanced
    metadata["matches_failed"] = matches_failed
    metadata["retry_advanced_boxscores"] = {
        "retry_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "retry_total": len(matches_to_retry),
        "retry_success": retried_success,
        "retry_failed": retried_failed,
    }
    data["metadata"] = metadata
    data["matches"] = matches

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Saved retried data to {output_path}")
        print(f"  - Retried: {len(matches_to_retry)}")
        print(f"  - Retried success: {retried_success}")
        print(f"  - Retried failed: {retried_failed}")
        print(f"  - Matches with boxscore: {matches_with_advanced}")
        print(f"  - Matches failed: {matches_failed}")
        print(f"{'=' * 60}")


def download_league_boxscores_all_seasons(
    category_id: str,
    output_dir: str,
    start_year: int = 2010,
    limit_seasons: Optional[int] = None,
    combine_output: bool = False,
    combined_file: Optional[str] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download boxscores + player data for all seasons from start_year to newest.

    Historical seasons (pre-2022) are fetched from BasketHotel.
    Modern seasons (2022+) use season-comprehensive with advanced boxscores.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"\n{'=' * 80}")
        print("LEAGUE BOXSCORES (ALL SEASONS)")
        print(f"{'=' * 80}")
        print(f"Category ID: {category_id}")
        print(f"Start year: {start_year}")
        print(f"Output directory: {output_path.absolute()}")
        print(f"{'=' * 80}\n")

    try:
        category_data = BasketFiAPI.get_category("huki2526", category_id)
    except Exception as e:
        print(f"Error: Failed to fetch category info: {e}")
        return

    if "category" not in category_data or "seasons" not in category_data["category"]:
        print(f"Error: Could not retrieve seasons for category-id ({category_id}).")
        return

    category = category_data["category"]
    category_name = category.get("category_name", str(category_id))
    safe_category = (
        category_name.lower().replace(" ", "_").replace("/", "_").replace("__", "_")
    )

    seasons = category["seasons"]
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)

    filtered = []
    for season in seasons:
        season_name = season.get("season_name")
        season_year = _extract_season_start_year(season_name)
        if season_year is None or season_year < start_year:
            continue
        filtered.append(season)

    if not filtered:
        print("No seasons found for the requested year range.")
        return

    if limit_seasons is not None:
        filtered = filtered[:limit_seasons]

    if verbose:
        print(f"Found {len(filtered)} seasons to download.")

    combined_payload = {
        "metadata": {
            "category_id": category_id,
            "category_name": category_name,
            "start_year": start_year,
            "limit_seasons": limit_seasons,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "seasons": [],
    }

    for idx, season in enumerate(filtered, 1):
        season_name = season.get("season_name") or season.get("competition_id")
        competition_id = season.get("competition_id") or season.get("season_id")
        is_historical = _is_historical_season(season)

        if verbose:
            print(f"\n[{idx}/{len(filtered)}] Processing season {season_name}")

        if is_historical:
            output_file = output_path / f"{safe_category}_{season_name}_baskethotel.json"
            target_file = (
                output_path / f".tmp_{safe_category}_{season_name}_baskethotel.json"
                if combine_output
                else output_file
            )
            download_baskethotel_season_boxscores(
                category_id=category_id,
                season_id=season_name or str(competition_id),
                output_file=str(target_file),
                max_workers=max_workers,
                verbose=verbose,
            )
        else:
            output_file = output_path / f"{safe_category}_{season_name}_genius.json"
            target_file = (
                output_path / f".tmp_{safe_category}_{season_name}_genius.json"
                if combine_output
                else output_file
            )
            download_season_comprehensive(
                category_id=category_id,
                competition_id=str(competition_id or season_name),
                output_file=str(target_file),
                season_name=season_name,
                include_advanced=True,
                max_workers=max_workers,
                verbose=verbose,
            )

        if combine_output:
            try:
                season_payload = json.loads(target_file.read_text(encoding="utf-8"))
                season_payload["season_name"] = season_name
                season_payload["source"] = (
                    "baskethotel" if is_historical else "genius"
                )
                combined_payload["seasons"].append(season_payload)
            finally:
                if target_file.exists():
                    target_file.unlink()

    if combine_output:
        combined_path = (
            Path(combined_file)
            if combined_file
            else output_path / f"{safe_category}_combined.json"
        )
        with open(combined_path, "w", encoding="utf-8") as f:
            json.dump(combined_payload, f, indent=2, ensure_ascii=False)
        if verbose:
            print(f"\nSaved combined output to {combined_path}")


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
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)
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
        is_historical = _is_historical_season(season)

        if verbose:
            print(f"[{idx}/{len(seasons)}] Processing season: {season_name}")

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
                    print("  - Using BasketHotel API for historical season data")
                processed_matches_raw = _fetch_historical_matches(
                    matches=matches,
                    season_name=season_name,
                    season_id=str(season_data_id) if season_data_id else None,
                    category_id=category_id,
                    category_name=category_name,
                    max_workers=max_workers,
                    verbose=verbose,
                )
                total_matches_found += len(processed_matches_raw)
            else:
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
                if is_historical:
                    print(
                        f"  ✓ Found {len(processed_matches)} played matches"
                    )
                else:
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
                        session = _get_genius_session(max_workers)
                        boxscore = GeniusSportsAPI.get_match_boxscore(
                            str(match_info["external_id"]),
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
                        desc=f"  Fetching advanced stats ({season_name})",
                        disable=not verbose,
                    ) as pbar:
                        for future in as_completed(futures):
                            match_info = futures[future]
                            index, boxscore, error, error_type = future.result()

                            if boxscore:
                                all_matches[index]["boxscore"] = normalize_boxscore(
                                    boxscore, source="genius"
                                )
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
                    "total_matches": len(processed_matches_raw)
                    if is_historical
                    else len(matches),
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


def download_baskethotel_season_boxscores(
    category_id: str,
    season_id: str,
    output_file: str,
    limit_games: Optional[int] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download BasketHotel boxscores (with player rows) for every game in a season.

    Args:
        category_id: League category identifier (e.g., "4" for Korisliiga)
        season_id: BasketHotel season name (e.g., "2015-2016") or season ID
        output_file: Path where output file will be saved
        max_workers: Number of concurrent workers for parallel downloads
        verbose: Whether to show progress output
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    league_id = _get_baskethotel_league_id(category_id)
    resolved_season_id = (
        _resolve_baskethotel_season_id(season_id, league_id) or season_id
    )

    if verbose:
        print(f"Fetching BasketHotel boxscores for {season_id}...")
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
            boxscore = client.fetch_boxscore_data(
                game_id=str(game_id),
                season_id=str(resolved_season_id),
                league_id=str(league_id),
                session=session,
            )
            return (boxscore, None)
        except Exception as exc:
            return (None, str(exc))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_game, game_id): game_id for game_id in game_ids
        }
        with tqdm(
            total=len(game_ids),
            desc="Fetching BasketHotel boxscores",
            disable=not verbose,
        ) as pbar:
            for future in as_completed(futures):
                game_id = futures[future]
                boxscore, error = future.result()
                if boxscore:
                    game_info = boxscore.get("game_info", {})
                    teams_info = boxscore.get("game_teams", {})
                    home_team = teams_info.get("home", {}).get("name")
                    away_team = teams_info.get("away", {}).get("name")
                    normalized_boxscore = normalize_boxscore(
                        boxscore, source="baskethotel"
                    )
                    teams = normalized_boxscore.get("teams", [])
                    teams_by_name = {
                        team.get("team_name"): team
                        for team in teams
                        if team.get("team_name")
                    }

                    def _normalize_team_name(name: Optional[str]) -> str:
                        if not name:
                            return ""
                        return re.sub(r"[^a-z0-9]", "", name.lower())

                    home_score = None
                    away_score = None
                    if home_team:
                        home_key = _normalize_team_name(home_team)
                        for team_name, team in teams_by_name.items():
                            team_key = _normalize_team_name(team_name)
                            if team_key and (team_key in home_key or home_key in team_key):
                                home_score = team["totals"].get("points")
                                team["team_name"] = home_team
                                for player in team.get("players", []):
                                    player["team"] = home_team
                                break
                    if away_team:
                        away_key = _normalize_team_name(away_team)
                        for team_name, team in teams_by_name.items():
                            team_key = _normalize_team_name(team_name)
                            if team_key and (team_key in away_key or away_key in team_key):
                                away_score = team["totals"].get("points")
                                team["team_name"] = away_team
                                for player in team.get("players", []):
                                    player["team"] = away_team
                                break

                    if home_score is None or away_score is None:
                        if len(teams) == 2:
                            if home_score is None:
                                home_score = teams[0]["totals"].get("points")
                                if home_team:
                                    teams[0]["team_name"] = home_team
                                    for player in teams[0].get("players", []):
                                        player["team"] = home_team
                            if away_score is None:
                                away_score = teams[1]["totals"].get("points")
                                if away_team:
                                    teams[1]["team_name"] = away_team
                                    for player in teams[1].get("players", []):
                                        player["team"] = away_team
                    status = "Played" if home_score is not None and away_score is not None else None

                    matches_data.append(
                        {
                            "match_id": str(game_id),
                            "match_external_id": None,
                            "date": game_info.get("date"),
                            "time": game_info.get("time"),
                            "home_team": home_team,
                            "home_team_id": None,
                            "away_team": away_team,
                            "away_team_id": None,
                            "home_score": home_score,
                            "away_score": away_score,
                            "status": status,
                            "venue": game_info.get("venue"),
                            "competition": season_id,
                            "category": None,
                            "season": season_id,
                            "boxscore": normalized_boxscore,
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
            "total_matches_in_season": None,
            "total_games_requested": len(game_ids),
            "played_matches_saved": len(matches_data),
            "matches_with_boxscore": len(matches_data),
            "matches_failed": len(errors),
            "include_advanced_stats": True,
            "limit_games": limit_games,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "matches": matches_data,
        "errors": errors,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"✓ Successfully fetched {len(matches_data)}/{len(game_ids)} games")
        if errors:
            print(f"  - Failed: {len(errors)}")
        print(f"  - Saved to: {output_path}")
        print(f"{'=' * 60}")


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
                                        error_display = f"{error_display} {match_info['url']}"

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
                """Fetch box score for a single match."""
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
                            processed_matches[index]["boxscore"] = normalize_boxscore(
                                boxscore, source="genius"
                            )
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
                1 for m in result_data["matches"] if "boxscore" in m
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
                        matches_to_fetch_advanced.append(
                            {
                                "index": match_idx,
                                "external_id": external_id,
                                "home_team": match_data["home_team"],
                                "away_team": match_data["away_team"],
                                "match_date": match_data.get("date")
                                or match_data.get("match_date")
                                or "Unknown date",
                                "url": f"https://hosted.dcd.shared.geniussports.com/FBAA/en/match/{external_id}/boxscore",
                            }
                        )

            total_played_matches += len(processed_matches)

            if verbose:
                if is_historical:
                    print(
                        f"    ✓ Found {len(processed_matches)} played matches"
                    )
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
                                processed_matches[index]["boxscore"] = normalize_boxscore(
                                    boxscore, source="genius"
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
                                            error_display = f"{error_display} {match_info['url']}"

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
  
  # Option 1b: Match list with boxscores (optionally limit games)
  uv run koris-api season-boxscores --category-id 4 --season-id 2024-2025 --adv-players --limit-games 1 --output season_boxscores.json

  # Option 2: All matches of one team from one season
  uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output team.json

  # Option 2b: Historical season boxscores (BasketHotel)
  uv run koris-api season-baskethotel-boxscores --category-id 4 --season-id 2015-2016 --output season_boxscores.json

  # Option 3: All seasons with all teams and their matches
  uv run koris-api league-comprehensive --category-id 4 --output-dir korisliiga_data

  # Option 6: All seasons from 2010 with player boxscores
  uv run koris-api league-boxscores-all-seasons --category-id 4 --output-dir korisliiga_boxscores

  # Option 4: Season averages for rebounds/assists/steals (Genius Sports boxscores)
  uv run koris-api season-advanced-averages --category-id 4 --season-id huki2526 --all-seasons --output season_avgs.json

  # Option 5: Historical season game leaders (BasketHotel boxscores)
  uv run koris-api season-game-leaders --category-id 4 --season-id 2015-2016 --output season_leaders.json

  # Option 7: Retry advanced boxscores that failed with 404
  uv run koris-api retry-advanced-404s --input season_boxscores_2023-2024.json --output season_boxscores_2023-2024_retry.json

  # Add --adv-players to include per-match player stats from advanced boxscores
  # Add --adv-teams to include team season statistics (averages, shooting, totals)
  # Add --cache-file to reuse boxscore summaries across runs

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
        nargs="?",
        choices=[
            "season-comprehensive",
            "season-boxscores",
            "season-baskethotel-boxscores",
            "team-season",
            "league-comprehensive",
            "league-boxscores-all-seasons",
            "season-advanced-averages",
            "season-game-leaders",
            "retry-advanced-404s",
        ],
        help="Action to perform",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for options interactively",
    )
    parser.add_argument(
        "--season-id",
        default="huki2526",
        help="Season ID (default: huki2526 for current season)",
    )
    parser.add_argument(
        "--season-ids",
        help="Comma-separated season IDs to process (overrides --season-id)",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2010,
        help="Start year for all-seasons downloads (default: 2010)",
    )
    parser.add_argument(
        "--limit-seasons",
        type=int,
        help="Limit the number of most recent seasons to download (all-seasons actions)",
    )
    parser.add_argument(
        "--combine-output",
        action="store_true",
        help="Combine season outputs into a single JSON file (all-seasons actions)",
    )
    parser.add_argument(
        "--combined-file",
        help="Path for combined output JSON when --combine-output is set",
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
        "--input",
        help="Input JSON file (retry-advanced-404s)",
    )
    parser.add_argument(
        "--limit-games",
        type=int,
        help="Limit number of games fetched (useful for samples)",
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
        "--all-seasons",
        action="store_true",
        help="Include all seasons for the category (season-advanced-averages)",
    )
    parser.add_argument(
        "--cache-file",
        help="Cache file for advanced boxscore summaries (season-advanced-averages)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=40,
        help="Concurrent workers for advanced stats (default: 40)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    def _select_single_curses(
        title: str, options: list[tuple[str, str]], default: Optional[str]
    ) -> Optional[str]:
        def _run(stdscr):
            curses.curs_set(0)
            current = 0
            if default:
                for idx, (_, value) in enumerate(options):
                    if value == default:
                        current = idx
                        break
            while True:
                stdscr.erase()
                stdscr.addstr(0, 0, title)
                stdscr.addstr(1, 0, "Use arrows to move, Enter to select.")
                for idx, (label, _) in enumerate(options):
                    marker = "➤ " if idx == current else "  "
                    if idx == current:
                        stdscr.attron(curses.A_REVERSE)
                    stdscr.addstr(3 + idx, 0, f"{marker}{label}")
                    if idx == current:
                        stdscr.attroff(curses.A_REVERSE)
                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    current = (current - 1) % len(options)
                elif key in (curses.KEY_DOWN, ord("j")):
                    current = (current + 1) % len(options)
                elif key in (curses.KEY_ENTER, 10, 13):
                    return options[current][1]
                elif key in (27, ord("q")):
                    return None

        try:
            return curses.wrapper(_run)
        except curses.error:
            return None

    def _select_multi_curses(
        title: str, options: list[tuple[str, str]], defaults: list[str]
    ) -> Optional[list[str]]:
        def _run(stdscr):
            curses.curs_set(0)
            current = 0
            selected = {value for value in defaults}
            while True:
                stdscr.erase()
                stdscr.addstr(0, 0, title)
                stdscr.addstr(1, 0, "Use arrows to move, Space to toggle, Enter to confirm.")
                for idx, (label, value) in enumerate(options):
                    checked = "[x]" if value in selected else "[ ]"
                    marker = "➤ " if idx == current else "  "
                    line = f"{marker}{checked} {label}"
                    if idx == current:
                        stdscr.attron(curses.A_REVERSE)
                    stdscr.addstr(3 + idx, 0, line)
                    if idx == current:
                        stdscr.attroff(curses.A_REVERSE)
                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    current = (current - 1) % len(options)
                elif key in (curses.KEY_DOWN, ord("j")):
                    current = (current + 1) % len(options)
                elif key == ord(" "):
                    value = options[current][1]
                    if value in selected:
                        selected.remove(value)
                    else:
                        selected.add(value)
                elif key in (curses.KEY_ENTER, 10, 13):
                    return list(selected)
                elif key in (27, ord("q")):
                    return None

        try:
            return curses.wrapper(_run)
        except curses.error:
            return None

    def _prompt_single(
        title: str, options: list[tuple[str, str]], default: Optional[str]
    ) -> str:
        if sys.stdin.isatty() and options:
            selected = _select_single_curses(title, options, default)
            if selected is not None:
                return selected
        print(title)
        default_index = 1
        for idx, (label, value) in enumerate(options, start=1):
            print(f"  {idx}) {label}")
            if value == default:
                default_index = idx
        while True:
            choice = input(f"Select [default {default_index}]: ").strip()
            if not choice:
                return options[default_index - 1][1]
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                return options[int(choice) - 1][1]
            print("Enter a valid number.")

    def _prompt_multi(
        title: str, options: list[tuple[str, str]], defaults: list[str]
    ) -> list[str]:
        if sys.stdin.isatty() and options:
            selected = _select_multi_curses(title, options, defaults)
            if selected is not None:
                return selected
        print(title)
        default_indices = []
        for idx, (label, value) in enumerate(options, start=1):
            print(f"  {idx}) {label}")
            if value in defaults:
                default_indices.append(str(idx))
        prompt = (
            f"Select (space-separated) [default {' '.join(default_indices) or 'none'}]: "
        )
        while True:
            choice = input(prompt).strip()
            if not choice:
                return defaults
            tokens = choice.split()
            if all(t.isdigit() and 1 <= int(t) <= len(options) for t in tokens):
                return [options[int(t) - 1][1] for t in tokens]
            print("Enter space-separated numbers.")

    def _prompt_text(label: str, default: Optional[str], required: bool = False) -> str:
        prompt = f"{label} [{default}]: " if default else f"{label}: "
        while True:
            value = input(prompt).strip()
            if value:
                return value
            if default is not None:
                return default
            if not required:
                return ""
            print("Value required.")

    def _load_categories() -> list[tuple[str, str]]:
        categories_path = Path(__file__).parent.parent.parent / "categories.json"
        if categories_path.exists():
            data = json.loads(categories_path.read_text(encoding="utf-8"))
            return [
                (f"{cid}: {cdata.get('category_name', cid)}", str(cid))
                for cid, cdata in data.items()
            ]
        return [
            ("4: Korisliiga (Men's top division)", "4"),
            ("2: Miesten I divisioona A", "2"),
            ("13: Naisten Korisliiga", "13"),
        ]

    def _load_seasons(category_id: str) -> list[tuple[str, str]]:
        try:
            category_data = BasketFiAPI.get_category("huki2526", category_id)
            seasons = category_data.get("category", {}).get("seasons", [])
            seasons = _augment_seasons_with_baskethotel(seasons, category_id)
            return [
                (f"{s.get('season_name', s.get('competition_id'))} ({s.get('competition_id')})",
                 str(s.get("competition_id") or s.get("season_id")))
                for s in seasons
            ]
        except Exception:
            return []

    def _interactive(args: argparse.Namespace) -> argparse.Namespace:
        mode = _prompt_single(
            "Select download scope:",
            [("Season", "season"), ("League", "league")],
            "season",
        )

        categories = _load_categories()
        category_default = args.category_id or "4"
        args.category_id = _prompt_single(
            "Select league:", categories, category_default
        )

        season_default = args.season_id or "huki2526"
        args.season_ids = None
        if mode == "season":
            args.action = "season-boxscores"
            seasons = _load_seasons(args.category_id) if args.category_id else []
            if seasons:
                season_values = _prompt_multi(
                    "Select season(s):",
                    seasons,
                    [season_default],
                )
                if season_values:
                    args.season_ids = ",".join(season_values)
                    args.season_id = season_values[0]
            else:
                args.season_id = _prompt_text("Season ID", season_default, required=True)
        else:
            args.action = "league-comprehensive"
            args.season_id = season_default

        advanced = _prompt_single(
            "Download advanced boxscore?",
            [("Yes", "yes"), ("No", "no")],
            "no",
        )
        args.adv_players = advanced == "yes"
        args.adv_teams = False
        args.all_seasons = False
        args.combine_output = False

        return args

    if args.action is None or args.interactive:
        args = _interactive(args)

    season_ids = []
    if args.season_ids:
        season_ids = [s.strip() for s in args.season_ids.split(",") if s.strip()]

    def _output_for_season(base: Optional[str], season_id: str, prefix: str) -> str:
        if not base:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return f"{prefix}_{season_id}_{timestamp}.json"
        path = Path(base)
        if path.suffix:
            return str(path.with_name(f"{path.stem}_{season_id}{path.suffix}"))
        return f"{base}_{season_id}.json"

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
            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(args.output, season_id, "season")

                # Get season name from category data if possible
                season_name = None
                try:
                    category_data = BasketFiAPI.get_category(
                        season_id, args.category_id
                    )
                    if (
                        "category" in category_data
                        and "seasons" in category_data["category"]
                    ):
                        for season in category_data["category"]["seasons"]:
                            if season.get("competition_id") == season_id:
                                season_name = season.get("season_name")
                                break
                except Exception:
                    pass  # Use competition_id as season_name if we can't get it

                download_season_comprehensive(
                    category_id=args.category_id,
                    competition_id=season_id,
                    output_file=output_file,
                    season_name=season_name,
                    include_advanced=args.adv_players,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 1b: Match list with boxscores (optional limit)
        elif args.action == "season-boxscores":
            if not args.season_id:
                print("Error: --season-id is required for season-boxscores action")
                print(
                    "Example: uv run koris-api season-boxscores --category-id 4 --season-id 2024-2025 --adv-players --limit-games 1 --output season_boxscores.json"
                )
                return
            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_boxscores"
                )
                download_matches_with_boxscores(
                    season_id=season_id,
                    category_id=args.category_id,
                    output_file=output_file,
                    include_advanced=args.adv_players,
                    limit_games=args.limit_games,
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

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, f"{args.team_id}_{season_id}", "team"
                )

                # Get season name from category data if possible (and if category_id is provided)
                season_name = None
                if args.category_id:
                    try:
                        category_data = BasketFiAPI.get_category(
                            season_id, args.category_id
                        )
                        if (
                            "category" in category_data
                            and "seasons" in category_data["category"]
                        ):
                            for season in category_data["category"]["seasons"]:
                                if season.get("competition_id") == season_id:
                                    season_name = season.get("season_name")
                                    break
                    except Exception:
                        pass

                download_team_season(
                    team_id=args.team_id,
                    category_id=args.category_id,
                    competition_id=season_id,
                    output_file=output_file,
                    season_name=season_name,
                    include_advanced=args.adv_players,
                    include_team_stats=args.adv_teams,
                    genius_competition_id=args.genius_competition_id,
                    genius_team_id=args.genius_team_id,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 2b: BasketHotel boxscores for a historical season
        elif args.action == "season-baskethotel-boxscores":
            if not args.category_id:
                print("Error: --category-id is required for season-baskethotel-boxscores")
                print(
                    "Example: uv run koris-api season-baskethotel-boxscores --category-id 4 --season-id 2015-2016 --output season_boxscores.json"
                )
                return

            if not args.season_id:
                print("Error: --season-id is required for season-baskethotel-boxscores")
                return

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_baskethotel_boxscores"
                )
                download_baskethotel_season_boxscores(
                    category_id=args.category_id,
                    season_id=season_id,
                    output_file=output_file,
                    limit_games=args.limit_games,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 3: All seasons with all teams and their matches
        elif args.action == "league-comprehensive":
            # Validate output directory
            if not args.output_dir:
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

        # Option 3b: All seasons from start year with player boxscores
        elif args.action == "league-boxscores-all-seasons":
            if not args.output_dir:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                args.output_dir = f"league_boxscores_{args.category_id}_{timestamp}"

            download_league_boxscores_all_seasons(
                category_id=args.category_id,
                output_dir=args.output_dir,
                start_year=args.start_year,
                limit_seasons=args.limit_seasons,
                combine_output=args.combine_output,
                combined_file=args.combined_file,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

        # Option 4: Season averages for rebounds/assists/steals from advanced boxscores
        elif args.action == "season-advanced-averages":
            if not args.category_id:
                print("Error: --category-id is required for season-advanced-averages")
                print(
                    "Example: uv run koris-api season-advanced-averages --category-id 4 --season-id huki2526 --all-seasons --output season_avgs.json"
                )
                return

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_advanced_averages"
                )
                download_season_advanced_averages(
                    category_id=args.category_id,
                    season_id=season_id,
                    output_file=output_file,
                    all_seasons=args.all_seasons,
                    cache_file=args.cache_file,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 5: Historical season game leaders from BasketHotel boxscores
        elif args.action == "season-game-leaders":
            if not args.category_id:
                print("Error: --category-id is required for season-game-leaders")
                print(
                    "Example: uv run koris-api season-game-leaders --category-id 4 --season-id 2015-2016 --output season_leaders.json"
                )
                return

            if not args.season_id:
                print("Error: --season-id is required for season-game-leaders")
                return

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_game_leaders"
                )
                download_season_game_leaders(
                    category_id=args.category_id,
                    season_id=season_id,
                    output_file=output_file,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )
        elif args.action == "retry-advanced-404s":
            if not args.input:
                print("Error: --input is required for retry-advanced-404s")
                print(
                    "Example: uv run koris-api retry-advanced-404s --input season_boxscores_2023-2024.json"
                )
                return

            retry_advanced_boxscores_404s(
                input_file=args.input,
                output_file=args.output,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

    except Exception as e:
        print(f"Error: {e}")
