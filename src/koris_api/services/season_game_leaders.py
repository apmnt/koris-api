import asyncio
import json
import time
from pathlib import Path

import httpx
from tqdm import tqdm

from ..baskethotel_api import BasketHotelAPI
from ..boxscore_normalizer import normalize_boxscore
from .common import (
    _BASKETHOTEL_DEFAULT_SEASON_ID,
    _clean_player_name,
    _fetch_baskethotel_schedule_game_ids,
    _get_baskethotel_league_id,
    _is_historical_season,
    _parse_baskethotel_date,
    _resolve_baskethotel_season_id,
    _stat_int,
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
