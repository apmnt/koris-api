"""Genius Sports HTML scraping client for advanced box scores and player data."""

import requests
from typing import Dict, Any, Optional, List, cast, Callable
import time
import json
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag
import re
from .genius_parser import GeniusSportsParser


class GeniusSportsBoxscoreError(Exception):
    def __init__(
        self,
        match_id: str,
        url: Optional[str] = None,
        status_code: Optional[int] = None,
        error_type: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        self.match_id = match_id
        self.url = url
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(
            message
            or f"Failed to fetch Genius Sports boxscore for match {match_id}"
        )


class GeniusSportsAPI:
    """Client for scraping basketball data from Genius Sports hosted pages."""

    _DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,fi;q=0.8",
    }

    @classmethod
    def get_match_boxscore(
        cls,
        match_id: str,
        competition_id: Optional[str] = None,
        session: Optional[requests.Session] = None,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 15.0,
        not_found_retries: int = 0,
        not_found_backoff_seconds: float = 2.0,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch and parse box score data from the Genius Sports hosted page.

        Args:
            match_id: The match identifier from Genius Sports
            competition_id: Optional Genius Sports competition identifier
            not_found_retries: Unused (404s are not retried)
            not_found_backoff_seconds: Unused (404s are not retried)
            log_fn: Optional callback for retry logging

        Returns:
            Dictionary containing parsed box score data with team stats and player stats
        """
        html, _ = cls.fetch_match_boxscore_html(
            match_id=match_id,
            competition_id=competition_id or "12345",
            session=session,
            retries=retries,
            backoff_seconds=backoff_seconds,
            timeout_seconds=timeout_seconds,
            not_found_retries=not_found_retries,
            not_found_backoff_seconds=not_found_backoff_seconds,
            log_fn=log_fn,
        )
        return GeniusSportsParser.parse_boxscore_html(html)

    @classmethod
    def fetch_match_boxscore_html(
        cls,
        match_id: str,
        competition_id: Optional[str] = None,
        session: Optional[requests.Session] = None,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 15.0,
        not_found_retries: int = 0,
        not_found_backoff_seconds: float = 2.0,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        url_templates = cls._get_match_boxscore_url_templates(
            match_id=match_id, competition_id=competition_id or "12345"
        )
        client = session or requests
        last_error: Optional[Exception] = None
        last_url: Optional[str] = None
        while True:
            last_error = None
            for url in [template.format(match_id=match_id) for template in url_templates]:
                last_url = url
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        response = client.get(
                            url,
                            timeout=timeout_seconds,
                            headers=None if session is not None else cls._DEFAULT_HEADERS,
                        )
                        if response.status_code == 404:
                            last_error = GeniusSportsBoxscoreError(
                                match_id=match_id,
                                url=url,
                                status_code=404,
                                error_type="HTTPError",
                                message="HTTP 404 Not Found",
                            )
                            break
                        if response.status_code >= 500 and attempt <= retries:
                            time.sleep(backoff_seconds * attempt)
                            continue
                        response.raise_for_status()
                        return response.text, url
                    except requests.exceptions.ReadTimeout as exc:
                        last_error = exc
                        if attempt <= retries:
                            time.sleep(backoff_seconds * attempt)
                            continue
                        break
                    except requests.exceptions.HTTPError as exc:
                        last_error = exc
                        if exc.response is not None and exc.response.status_code == 404:
                            last_error = GeniusSportsBoxscoreError(
                                match_id=match_id,
                                url=url,
                                status_code=404,
                                error_type="HTTPError",
                                message=str(exc),
                            )
                            break
                        if attempt <= retries:
                            time.sleep(backoff_seconds * attempt)
                            continue
                        break

            break

        if isinstance(last_error, GeniusSportsBoxscoreError):
            raise last_error
        if isinstance(last_error, requests.exceptions.HTTPError):
            status_code = (
                last_error.response.status_code if last_error.response else None
            )
            raise GeniusSportsBoxscoreError(
                match_id=match_id,
                url=last_url,
                status_code=status_code,
                error_type="HTTPError",
                message=str(last_error),
            )
        if isinstance(last_error, requests.exceptions.ReadTimeout):
            raise GeniusSportsBoxscoreError(
                match_id=match_id,
                url=last_url,
                status_code=None,
                error_type="ReadTimeout",
                message=str(last_error),
            )
        if last_error:
            raise GeniusSportsBoxscoreError(
                match_id=match_id,
                url=last_url,
                status_code=None,
                error_type=type(last_error).__name__,
                message=str(last_error),
            )
        raise GeniusSportsBoxscoreError(match_id=match_id, url=last_url)

    @classmethod
    def _get_match_boxscore_url_templates(
        cls, match_id: str, competition_id: Optional[str] = None
    ) -> List[str]:
        competition_id = competition_id or "12345"
        return [
            f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/match/{match_id}/boxscore",
            f"https://hosted.dcd.shared.geniussports.com/FBAA/fi/competition/{competition_id}/match/{match_id}/boxscore",
        ]

    @classmethod
    def build_match_boxscore_url(
        cls, match_id: str, competition_id: Optional[str] = None, language: str = "en"
    ) -> str:
        competition_id = competition_id or "12345"
        return (
            "https://hosted.dcd.shared.geniussports.com/FBAA/"
            f"{language}/competition/{competition_id}/match/{match_id}/boxscore"
        )

    @classmethod
    def get_match_playbyplay(cls, match_id: str) -> Dict[str, Any]:
        """
        Fetch and parse play-by-play data from the Genius Sports hosted page.

        Args:
            match_id: The match identifier from Genius Sports

        Returns:
            Dictionary containing parsed play-by-play data with match info and events
        """
        url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/42145/match/{match_id}/playbyplay"
        response = requests.get(url)
        response.raise_for_status()

        return GeniusSportsParser.parse_playbyplay_html(response.text)

    @classmethod
    def get_genius_teams(cls, competition_id: str) -> List[Dict[str, Any]]:
        """
        Fetch teams from Genius Sports teams page for a specific competition.

        Args:
            competition_id: The Genius Sports competition identifier

        Returns:
            List of dictionaries containing team data (id, name)
        """
        url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/teams"
        response = requests.get(url)
        response.raise_for_status()

        return GeniusSportsParser.parse_teams_page(response.text)

    @classmethod
    def get_genius_players(
        cls, competition_id: str, output_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch all players and their gamelogs from Genius Sports for a specific competition.

        Args:
            competition_id: The Genius Sports competition identifier
            output_file: Optional path to save the results as JSON

        Returns:
            Dictionary containing players data with their gamelogs
        """
        # First, fetch all teams in the competition
        print(f"Fetching teams for competition {competition_id}...")
        teams = cls.get_genius_teams(competition_id)
        teams_dict = {team["id"]: team["name"] for team in teams}
        print(f"Found {len(teams)} teams")

        # Fetch the players list page
        print("Fetching players list...")
        players_url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/players"
        response = requests.get(players_url)
        response.raise_for_status()

        # Parse player links from the page
        player_links_html = GeniusSportsParser.parse_players_page(response.text)
        print(f"Found {len(player_links_html)} players")

        result: Dict[str, Any] = {
            "competition_id": competition_id,
            "teams": teams,
            "players": [],
        }

        # Process each player
        for idx, player_link in enumerate(player_links_html, 1):
            player_id = player_link["id"]
            player_name = player_link["name"]
            print(f"Processing player {idx}/{len(player_links_html)}: {player_name}")

            # Fetch player's gamelog
            gamelog_url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/person/{player_id}/gamelog"

            try:
                gamelog_response = requests.get(gamelog_url)
                gamelog_response.raise_for_status()

                # Parse gamelog
                gamelog_data = GeniusSportsParser.parse_player_gamelog(
                    gamelog_response.text, teams_dict
                )

                player_data = {
                    "id": player_id,
                    "name": player_name,
                    "team": gamelog_data.get("team"),
                    "team_id": gamelog_data.get("team_id"),
                    "games": gamelog_data.get("games", []),
                }

                result["players"].append(player_data)

            except Exception as e:
                print(f"  Error fetching gamelog for {player_name}: {e}")
                # Add player anyway, but without gamelog data
                result["players"].append(
                    {
                        "id": player_id,
                        "name": player_name,
                        "team": None,
                        "team_id": None,
                        "games": [],
                        "error": str(e),
                    }
                )

        # Save to file if specified
        if output_file:
            output_path = Path(output_file)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"\nSaved data to {output_file}")

        return result

    @classmethod
    def get_genius_players_by_team(
        cls,
        competition_id: str,
        team_id: str,
        output_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch players and their gamelogs for a specific team from Genius Sports.

        Args:
            competition_id: The Genius Sports competition identifier
            team_id: The Genius Sports team identifier
            output_file: Optional path to save the results as JSON

        Returns:
            Dictionary containing players data with their gamelogs for the specified team
        """
        # First, fetch all teams in the competition to get team name
        print(f"Fetching teams for competition {competition_id}...")
        teams = cls.get_genius_teams(competition_id)
        teams_dict = {team["id"]: team["name"] for team in teams}

        # Find the team name
        team_name = teams_dict.get(team_id, f"Team {team_id}")
        print(f"Fetching roster for team: {team_name} (ID: {team_id})")

        # Fetch the team roster page
        team_url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/team/{team_id}"
        response = requests.get(team_url)
        response.raise_for_status()

        # Parse player links from the roster page
        unique_players = GeniusSportsParser.parse_team_roster_page(response.text)
        print(f"Found {len(unique_players)} players on roster")

        # Print the player names first
        print(f"\n{team_name} roster:")
        for idx, player in enumerate(unique_players, 1):
            print(f"  {idx}. {player['name']} (ID: {player['id']})")
        print()

        result: Dict[str, Any] = {
            "competition_id": competition_id,
            "team_id": team_id,
            "team_name": team_name,
            "teams": teams,
            "players": [],
        }

        # Process each player
        for idx, player_info in enumerate(unique_players, 1):
            player_id = player_info["id"]
            player_name = player_info["name"]

            print(
                f"[{idx}/{len(unique_players)}] Fetching gamelog for {player_name}..."
            )

            # Fetch player's gamelog
            gamelog_url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/person/{player_id}/gamelog"

            try:
                gamelog_response = requests.get(gamelog_url)
                gamelog_response.raise_for_status()

                # Parse gamelog
                gamelog_data = GeniusSportsParser.parse_player_gamelog(
                    gamelog_response.text, teams_dict
                )

                player_data = {
                    "id": player_id,
                    "name": player_name,
                    "team": team_name,  # Use the team we're querying for, not from gamelog
                    "team_id": team_id,  # Use the team ID we're querying for
                    "games": gamelog_data.get("games", []),
                }

                result["players"].append(player_data)
                print(f"  ✓ Found {len(gamelog_data.get('games', []))} games")

            except Exception as e:
                print(f"  ✗ Error: {e}")
                # Add player anyway, but without gamelog data
                result["players"].append(
                    {
                        "id": player_id,
                        "name": player_name,
                        "team": team_name,
                        "team_id": team_id,
                        "games": [],
                        "error": str(e),
                    }
                )

        print(f"\nCompleted! Found {len(result['players'])} players for {team_name}")

        # Save to file if specified
        if output_file:
            output_path = Path(output_file)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"Saved data to {output_file}")

        return result

    @classmethod
    def get_team_statistics(
        cls,
        competition_id: str,
        team_id: str,
        output_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch team player statistics from Genius Sports.

        Args:
            competition_id: The Genius Sports competition identifier
            team_id: The Genius Sports team identifier
            output_file: Optional path to save the results as JSON

        Returns:
            Dictionary containing team statistics with averages, shooting, and totals
        """
        print(
            f"Fetching team statistics for team {team_id} in competition {competition_id}..."
        )

        # Fetch the team statistics page
        url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/team/{team_id}/statistics"
        response = requests.get(url)
        response.raise_for_status()

        # Parse the statistics
        stats = GeniusSportsParser.parse_team_statistics_page(response.text)

        result: Dict[str, Any] = {
            "competition_id": competition_id,
            "team_id": team_id,
            **stats,
        }

        print(f"Found statistics for {len(stats.get('averages', []))} players")

        # Save to file if specified
        if output_file:
            output_path = Path(output_file)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"Saved data to {output_file}")

        return result
