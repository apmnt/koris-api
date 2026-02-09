import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import requests
import sys
from tqdm import tqdm

from ..basketfi_api import BasketFiAPI
from ..baskethotel_api import BasketHotelAPI
from ..baskethotel_parser import BasketHotelParser


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


def resolve_genius_competition_id(
    category_id: Optional[str],
    season_id: Optional[str] = None,
    match_category_external_id: Optional[str] = None,
    fallback_id: str = "12345",
) -> str:
    if match_category_external_id and str(match_category_external_id).strip():
        return str(match_category_external_id).strip()
    if category_id and season_id:
        ids = load_genius_ids(str(category_id), str(season_id))
        if ids:
            return str(ids[0])
    if category_id:
        ids = load_genius_ids(str(category_id), None)
        if ids:
            return str(ids[0])
    return fallback_id


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
        adapter = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64)
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
        return season_year <= 2023
    season_start_date = season.get("season_start_date")
    if season_start_date:
        try:
            return datetime.strptime(season_start_date, "%Y-%m-%d").year <= 2023
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

    requests_client = requests
    maybe_public = sys.modules.get("koris_api")
    if maybe_public and hasattr(maybe_public, "requests"):
        requests_client = getattr(maybe_public, "requests")

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
    response = requests_client.get(url, headers={"User-Agent": "Mozilla/5.0"})
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
    maybe_public = sys.modules.get("koris_api")
    resolve_season_id = (
        getattr(maybe_public, "_resolve_baskethotel_season_id", None)
        if maybe_public
        else None
    ) or _resolve_baskethotel_season_id
    fetch_team_map = (
        getattr(maybe_public, "_fetch_baskethotel_team_map", None)
        if maybe_public
        else None
    ) or _fetch_baskethotel_team_map
    fetch_schedule_ids = (
        getattr(maybe_public, "_fetch_baskethotel_schedule_game_ids", None)
        if maybe_public
        else None
    ) or _fetch_baskethotel_schedule_game_ids

    resolved_season_id = (
        resolve_season_id(season_name, league_id)
        or season_id
        or _BASKETHOTEL_DEFAULT_SEASON_ID
    )
    team_map = fetch_team_map(resolved_season_id, league_id)

    game_ids = fetch_schedule_ids(
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
        futures = {
            executor.submit(fetch_game, game_id): game_id for game_id in game_ids
        }

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
