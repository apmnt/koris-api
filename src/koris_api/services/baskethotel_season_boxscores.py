import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any

from tqdm import tqdm

from ..baskethotel_api import BasketHotelAPI
from ..boxscore_normalizer import normalize_boxscore
from .common import (
    _fetch_baskethotel_schedule_game_ids,
    _get_baskethotel_league_id,
    _get_baskethotel_session,
    _resolve_baskethotel_season_id,
)


def _attach_playbyplay_possession_counts(match_data: Dict[str, Any]) -> None:
    playbyplay = match_data.get("playbyplay")
    if not isinstance(playbyplay, dict):
        match_data["home_number_of_possessions"] = None
        match_data["away_number_of_possessions"] = None
        return

    possessions = playbyplay.get("possessions")
    if not isinstance(possessions, dict):
        match_data["home_number_of_possessions"] = None
        match_data["away_number_of_possessions"] = None
        return

    match_data["home_number_of_possessions"] = possessions.get("team1_possessions")
    match_data["away_number_of_possessions"] = possessions.get("team2_possessions")


def download_baskethotel_season_boxscores(
    category_id: str,
    season_id: str,
    output_file: str,
    limit_games: Optional[int] = None,
    max_workers: int = 5,
    include_playbyplay: bool = False,
    show_header: bool = True,
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

    if verbose and show_header:
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
    existing_matches_by_id: Dict[str, Dict[str, Any]] = {}
    existing_errors_by_id: Dict[str, Dict[str, Any]] = {}
    matches_index_by_id: Dict[str, int] = {}

    if output_path.exists():
        try:
            existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
            existing_matches = existing_payload.get("matches") or []
            if isinstance(existing_matches, list):
                for match in existing_matches:
                    match_id = match.get("match_id")
                    if match_id:
                        existing_matches_by_id[str(match_id)] = match
            existing_errors = existing_payload.get("errors") or []
            if isinstance(existing_errors, list):
                for err in existing_errors:
                    err_id = err.get("game_id")
                    if err_id:
                        existing_errors_by_id[str(err_id)] = err
        except Exception:
            existing_matches_by_id = {}
            existing_errors_by_id = {}

    def fetch_game(game_id: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            session = _get_baskethotel_session()
            boxscore = client.fetch_boxscore_data(
                game_id=str(game_id),
                season_id=str(resolved_season_id),
                league_id=str(league_id),
                session=session,
            )
            playbyplay = None
            playbyplay_error = None
            if include_playbyplay:
                try:
                    playbyplay = client.fetch_playbyplay_data(
                        game_id=str(game_id),
                        season_id=str(resolved_season_id),
                        league_id=str(league_id),
                        session=session,
                    )
                except Exception as exc:
                    playbyplay_error = str(exc)
            return ({"boxscore": boxscore, "playbyplay": playbyplay, "playbyplay_error": playbyplay_error}, None)
        except Exception as exc:
            return (None, str(exc))

    # Reuse existing matches/errors to avoid refetching.
    game_ids_to_fetch = []
    game_ids_to_fetch_playbyplay = []
    for game_id in game_ids:
        if str(game_id) in existing_matches_by_id:
            existing = existing_matches_by_id[str(game_id)]
            matches_index_by_id[str(game_id)] = len(matches_data)
            matches_data.append(existing)
            if include_playbyplay and not existing.get("playbyplay") and not existing.get(
                "playbyplay_error"
            ):
                game_ids_to_fetch_playbyplay.append(game_id)
        elif str(game_id) in existing_errors_by_id:
            errors.append(existing_errors_by_id[str(game_id)])
        else:
            game_ids_to_fetch.append(game_id)

    if verbose:
        print(
            f"Resume: {len(existing_matches_by_id)} with boxscore, "
            f"{len(existing_errors_by_id)} with error from existing file."
        )
        print(f"Resume: {len(game_ids_to_fetch)} games missing boxscores.")
        if include_playbyplay:
            print(
                f"Resume: {len(game_ids_to_fetch_playbyplay)} games missing play-by-play."
            )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_game, game_id): game_id
            for game_id in game_ids_to_fetch
        }
        with tqdm(
            total=len(game_ids_to_fetch),
            desc="Fetching BasketHotel boxscores",
            disable=not verbose,
        ) as pbar:
            for future in as_completed(futures):
                game_id = futures[future]
                payload, error = future.result()
                if payload:
                    boxscore = payload.get("boxscore", {})
                    playbyplay = payload.get("playbyplay")
                    playbyplay_error = payload.get("playbyplay_error")
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
                            if team_key and (
                                team_key in home_key or home_key in team_key
                            ):
                                home_score = team["totals"].get("points")
                                team["team_name"] = home_team
                                for player in team.get("players", []):
                                    player["team"] = home_team
                                break
                    if away_team:
                        away_key = _normalize_team_name(away_team)
                        for team_name, team in teams_by_name.items():
                            team_key = _normalize_team_name(team_name)
                            if team_key and (
                                team_key in away_key or away_key in team_key
                            ):
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
                    status = (
                        "Played"
                        if home_score is not None and away_score is not None
                        else None
                    )

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
                            "playbyplay": playbyplay,
                            "playbyplay_error": playbyplay_error,
                        }
                    )
                    _attach_playbyplay_possession_counts(matches_data[-1])
                    matches_index_by_id[str(game_id)] = len(matches_data) - 1
                else:
                    errors.append({"game_id": str(game_id), "error": error})
                    if verbose and error:
                        tqdm.write(f"  ✗ Game {game_id}: {error}")
                pbar.update(1)

    if include_playbyplay and game_ids_to_fetch_playbyplay:
        def fetch_playbyplay(game_id: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
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

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_playbyplay, game_id): game_id
                for game_id in game_ids_to_fetch_playbyplay
            }
            with tqdm(
                total=len(game_ids_to_fetch_playbyplay),
                desc="Fetching BasketHotel play-by-play",
                disable=not verbose,
            ) as pbar:
                for future in as_completed(futures):
                    game_id = futures[future]
                    playbyplay, error = future.result()
                    match_index = matches_index_by_id.get(str(game_id))
                    if match_index is not None:
                        if playbyplay:
                            matches_data[match_index]["playbyplay"] = playbyplay
                            _attach_playbyplay_possession_counts(matches_data[match_index])
                        else:
                            matches_data[match_index]["playbyplay_error"] = error
                            if verbose and error:
                                tqdm.write(f"  ✗ Game {game_id}: {error}")
                    pbar.update(1)

    for match in matches_data:
        _attach_playbyplay_possession_counts(match)

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
            "include_playbyplay": include_playbyplay,
            "limit_games": limit_games,
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
