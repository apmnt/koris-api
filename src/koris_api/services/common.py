import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm

from ..basketfi_api import BasketFiAPI
from ..baskethotel_api import BasketHotelAPI
from ..baskethotel_parser import BasketHotelParser
from ..pipeline.games import run_batch


def load_genius_ids(category_id: str, competition_id: str | None = None) -> list[str]:
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
    genius_ids: list[str] = []
    genius_ids_path = (
        Path(__file__).resolve().parents[1] / "catalogs" / "genius_ids.json"
    )
    try:
        catalog = json.loads(genius_ids_path.read_text(encoding="utf-8"))
        category = catalog.get(str(category_id), {})
        lookup = str(competition_id)
        year = _extract_season_start_year(competition_id)
        if lookup not in category and year is not None:
            lookup = f"{year}-{year + 1}"
        ids = category.get(lookup, []) if competition_id else []
        if isinstance(ids, list):
            genius_ids = [str(value) for value in ids if value]
    except (OSError, ValueError, AttributeError):
        genius_ids = []
    if not genius_ids and competition_id:
        response = BasketFiAPI.get_category(competition_id, category_id)
        external_id = response.get("category", {}).get("category_external_id")
        if external_id and str(external_id).strip():
            genius_ids.append(str(external_id).strip())
    return list(dict.fromkeys(genius_ids))


def resolve_genius_competition_id(
    category_id: str | None,
    season_id: str | None = None,
    match_category_external_id: str | None = None,
    fallback_id: str | None = None,
) -> str | None:
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


_BASKETHOTEL_LEAGUE_ID_BY_CATEGORY = {
    "4": "4",  # Korisliiga (verified BasketHotel league selector)
    "2": "2",  # Miesten I divisioona A
}
_BASKETHOTEL_MIN_YEAR = 2010
_BASKETHOTEL_SESSION_LOCAL = threading.local()
_GENIUS_SESSION_LOCAL = threading.local()


def _get_baskethotel_league_id(category_id: str) -> str:
    league_id = _BASKETHOTEL_LEAGUE_ID_BY_CATEGORY.get(str(category_id))
    if league_id is None:
        raise ValueError(
            f"BasketHotel league mapping is unavailable for category {category_id}"
        )
    return league_id


def _get_baskethotel_session() -> requests.Session:
    session = getattr(_BASKETHOTEL_SESSION_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64)
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
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _GENIUS_SESSION_LOCAL.session = session
    return session


def _extract_season_start_year(season_name: str | None) -> int | None:
    if not season_name:
        return None
    if "-" in season_name:
        try:
            return int(season_name.split("-", 1)[0])
        except ValueError:
            return None
    return None


def _is_historical_season(season: dict[str, Any]) -> bool:
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


def _parse_baskethotel_date(date_text: str | None) -> str | None:
    if not date_text:
        return None
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_baskethotel_time(time_text: str | None) -> str | None:
    if not time_text:
        return None
    cleaned = time_text.strip()
    if len(cleaned) == 5 and cleaned[2] == ":":
        return f"{cleaned}:00"
    if len(cleaned) == 8 and cleaned[2] == ":" and cleaned[5] == ":":
        return cleaned
    return None


def _fetch_baskethotel_seasons(league_id: str) -> dict[str, str]:

    requests_client = requests
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
    response = requests_client.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30.0
    )
    response.raise_for_status()
    html = BasketHotelParser.extract_html_from_response(response.text)

    season_map: dict[str, str] = {}
    for match in re.findall(r'value="(\d+)"[^>]*>([^<]+)<', html):
        season_id, season_name = match
        season_map[season_name.strip()] = season_id

    return season_map


def _resolve_baskethotel_season_id(
    season_name: str | None, league_id: str
) -> str | None:
    if not season_name:
        return None
    season_map = _fetch_baskethotel_seasons(league_id)
    return season_map.get(season_name)


def available_season_rows(
    seasons: list[dict[str, Any]], category_id: str
) -> list[dict[str, Any]]:
    seasons = [dict(season) for season in seasons]
    try:
        league_id = _get_baskethotel_league_id(category_id)
    except ValueError:
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
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30.0)
    response.raise_for_status()
    return BasketHotelParser.extract_html_from_response(response.text)


def _fetch_baskethotel_team_map(season_id: str, league_id: str) -> dict[str, str]:

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
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30.0)
    response.raise_for_status()
    html = BasketHotelParser.extract_html_from_response(response.text)

    team_map: dict[str, str] = {}
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

    return team_map


def _extract_baskethotel_game_ids(html: str) -> list[str]:
    return re.findall(r"schedule-line-container-(\d+)", html)


def _extract_baskethotel_schedule_page_count(html: str) -> int:
    pages = [int(p) for p in re.findall(r'id="2-303-page-(\d+)"', html)]
    return max(pages) if pages else 1


def _fetch_baskethotel_schedule_game_ids(
    season_id: str, league_id: str, verbose: bool
) -> list[str]:
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
    base_match: dict[str, Any], game_data: dict[str, Any]
) -> dict[str, Any]:
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
    matches: list[dict[str, Any]],
    season_name: str,
    season_id: str | None,
    category_id: str,
    category_name: str | None,
    max_workers: int,
    verbose: bool,
) -> list[dict[str, Any]]:
    client = BasketHotelAPI()
    league_id = _get_baskethotel_league_id(category_id)
    resolve_season_id = _resolve_baskethotel_season_id
    fetch_team_map = _fetch_baskethotel_team_map
    fetch_schedule_ids = _fetch_baskethotel_schedule_game_ids

    resolved_season_id = resolve_season_id(season_name, league_id) or season_id
    if not resolved_season_id:
        raise ValueError("An explicit historical season is required")
    team_map = fetch_team_map(resolved_season_id, league_id)

    game_ids = fetch_schedule_ids(resolved_season_id, league_id, verbose)
    if not game_ids:
        return []

    def fetch_game(game_id: str) -> tuple[dict[str, Any] | None, str | None]:
        try:
            with requests.Session() as session:
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
            logging.getLogger(__name__).debug("common: operation failed", exc_info=True)
            return (None, str(exc))

    processed_matches: list[dict[str, Any]] = []
    games_failed = 0

    with tqdm(
        total=len(game_ids),
        desc=f"    Fetching historical games ({season_name})",
        disable=not verbose,
    ) as pbar:

        def progress(_done: int, _total: int) -> None:
            _ = pbar.update(1)

        results = run_batch(
            game_ids,
            fetch_game,
            max_workers=max_workers,
            progress=progress,
        )
    for game_id, (merged, error) in zip(game_ids, results, strict=True):
        if merged and all(
            merged.get(key) not in (None, "") for key in ("home_score", "away_score")
        ):
            processed_matches.append(merged)
        else:
            games_failed += 1
            if verbose and error:
                tqdm.write(f"      ✗ Game {game_id}: {error}")

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


def _extract_team_stat(team: dict[str, Any], stat_name: str) -> float:
    totals = team.get("totals", {})
    total_value = totals.get(stat_name)
    if total_value not in (None, ""):
        return _stat_value(total_value)

    total = 0.0
    for player in team.get("players", []):
        total += _stat_value(player.get(stat_name, 0))
    return total


def _summarize_boxscore(boxscore: dict[str, Any]) -> dict[str, float]:
    totals = {"rebounds": 0.0, "assists": 0.0, "steals": 0.0}
    for team in boxscore.get("teams", []):
        totals["rebounds"] += _extract_team_stat(team, "Total Rebounds")
        totals["assists"] += _extract_team_stat(team, "Assists")
        totals["steals"] += _extract_team_stat(team, "Steals")
    return totals


def _summarize_baskethotel_boxscore(boxscore: dict[str, Any]) -> dict[str, float]:
    totals = {"rebounds": 0.0, "assists": 0.0, "steals": 0.0}
    for team in boxscore.get("teams", []):
        totals["rebounds"] += _stat_value(team.get("rebounds", 0))
        totals["assists"] += _stat_value(team.get("assists", 0))
        totals["steals"] += _stat_value(team.get("steals", 0))
    return totals
