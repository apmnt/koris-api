"""Genius Sports client with compatibility routing for current Sportradar games."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, cast

import requests

from .data import fields
from .genius_parser import GeniusSportsParser
from .sportradar_api import fetch_fixture, is_sportradar_id
from .sportradar_parser import parse_boxscore as parse_sportradar_boxscore
from .sportradar_parser import parse_playbyplay as parse_sportradar_playbyplay


class GeniusSportsBoxscoreError(Exception):
    def __init__(
        self,
        match_id: str,
        url: str | None = None,
        status_code: int | None = None,
        error_type: str | None = None,
        message: str | None = None,
    ) -> None:
        self.match_id = match_id
        self.url = url
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(
            message or f"Failed to fetch Genius Sports boxscore for match {match_id}"
        )


class GeniusSportsAPI:
    """Fetch game data, routing Basket.fi UUID fixtures to its Sportradar widget."""

    _DEFAULT_HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,fi;q=0.8",
    }

    @classmethod
    def get_match_boxscore(
        cls,
        match_id: str,
        competition_id: str | None = None,
        session: requests.Session | None = None,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 30.0,
        not_found_retries: int = 0,
        not_found_backoff_seconds: float = 2.0,
        log_fn: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
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
        if is_sportradar_id(match_id):
            return parse_sportradar_boxscore(
                fetch_fixture(
                    match_id, "statistics", timeout=timeout_seconds, session=session
                )
            )
        if not competition_id:
            return cls.get_match_boxscore_livestats(
                match_id,
                retries=retries,
                backoff_seconds=backoff_seconds,
                timeout_seconds=timeout_seconds,
            )
        html, _ = cls.fetch_match_boxscore_html(
            match_id=match_id,
            competition_id=competition_id,
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
        competition_id: str | None = None,
        session: requests.Session | None = None,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 30.0,
        not_found_retries: int = 0,
        not_found_backoff_seconds: float = 2.0,
        log_fn: Callable[[str], None] | None = None,
    ) -> tuple[str, str]:
        from .pipeline.transport import RequestsTransport, RetryPolicy
        from .pipeline.types import Request

        transport = RequestsTransport(
            session or requests, RetryPolicy(retries, timeout_seconds, backoff_seconds)
        )
        last_failure = None
        for url in cls._get_match_boxscore_url_templates(match_id, competition_id):
            result = transport.fetch(Request("boxscore", url, (), False))
            if result.failure is None and isinstance(result.payload, str):
                return result.payload, url
            last_failure = result.failure
            if log_fn is not None and last_failure is not None:
                log_fn(last_failure.message)
        raise GeniusSportsBoxscoreError(
            match_id,
            url=last_failure.url if last_failure else None,
            status_code=last_failure.status_code if last_failure else None,
            error_type=last_failure.kind if last_failure else None,
            message=last_failure.message if last_failure else None,
        )

    @classmethod
    def _get_match_boxscore_url_templates(
        cls, match_id: str, competition_id: str | None = None
    ) -> list[str]:
        if not competition_id:
            raise ValueError(
                "Genius HTML requires a competition_id; use the LiveStats feed without one"
            )
        return [
            f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/match/{match_id}/boxscore",
            f"https://hosted.dcd.shared.geniussports.com/FBAA/fi/competition/{competition_id}/match/{match_id}/boxscore",
        ]

    @classmethod
    def build_match_boxscore_url(
        cls, match_id: str, competition_id: str | None = None, language: str = "en"
    ) -> str:
        if not competition_id:
            raise ValueError(
                "Genius HTML requires a competition_id; use the LiveStats feed without one"
            )
        return (
            "https://hosted.dcd.shared.geniussports.com/FBAA/"
            f"{language}/competition/{competition_id}/match/{match_id}/boxscore"
        )

    @classmethod
    def get_match_playbyplay(
        cls,
        match_id: str,
        competition_id: str | None = None,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """
        Fetch and parse play-by-play data from the Genius Sports hosted page.

        Args:
            match_id: The match identifier from Genius Sports

        Returns:
            Dictionary containing parsed play-by-play data with match info and events
        """
        if is_sportradar_id(match_id):
            return dict(
                parse_sportradar_playbyplay(
                    fetch_fixture(match_id, "pbp", timeout=timeout_seconds)
                )
            )
        if not competition_id:
            return cls.get_match_playbyplay_livestats(
                match_id,
                retries=retries,
                backoff_seconds=backoff_seconds,
                timeout_seconds=timeout_seconds,
            )
        url = (
            "https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/"
            f"{competition_id}/match/{match_id}/playbyplay"
        )
        from .pipeline.transport import RequestsTransport, RetryPolicy
        from .pipeline.types import Request

        result = RequestsTransport(
            requests, RetryPolicy(retries, timeout_seconds, backoff_seconds)
        ).fetch(Request("playbyplay", url, (), False))
        if result.failure is not None:
            raise GeniusSportsBoxscoreError(
                match_id,
                url=result.failure.url,
                status_code=result.failure.status_code,
                error_type=result.failure.kind,
                message=result.failure.message,
            )
        return GeniusSportsParser.parse_playbyplay_html(str(result.payload))

    @classmethod
    def fetch_match_data_json(
        cls,
        match_id: str,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Fetch the FIBA LiveStats ``data.json`` feed for a match.

        Unlike the hosted ``competition/{id}/...`` HTML pages, this feed is keyed
        only by ``match_id`` and stays available for past and playoff matches, so
        it is the reliable source for box score and play-by-play data.
        """
        url = f"https://fibalivestats.dcd.shared.geniussports.com/data/{match_id}/data.json"
        from .pipeline.transport import RequestsTransport, RetryPolicy
        from .pipeline.types import Request

        result = RequestsTransport(
            requests, RetryPolicy(retries, timeout_seconds, backoff_seconds)
        ).fetch(Request("livestats", url, ()))
        if result.failure is not None:
            raise GeniusSportsBoxscoreError(
                match_id,
                url=result.failure.url,
                status_code=result.failure.status_code,
                error_type=result.failure.kind,
                message=result.failure.message,
            )
        if not isinstance(result.payload, dict):
            raise TypeError("LiveStats response must be an object")
        return cast(dict[str, Any], result.payload)

    @classmethod
    def get_match_boxscore_livestats(
        cls,
        match_id: str,
        data: dict[str, Any] | None = None,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Box score from the FIBA LiveStats feed (genius intermediate shape)."""
        from .genius_livestats_parser import parse_boxscore

        if data is None:
            data = cls.fetch_match_data_json(
                match_id,
                retries=retries,
                backoff_seconds=backoff_seconds,
                timeout_seconds=timeout_seconds,
            )
        return dict(parse_boxscore(data))

    @classmethod
    def get_match_playbyplay_livestats(
        cls,
        match_id: str,
        data: dict[str, Any] | None = None,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Play-by-play from the FIBA LiveStats feed (with possessions)."""
        from .genius_livestats_parser import parse_playbyplay

        if data is None:
            data = cls.fetch_match_data_json(
                match_id,
                retries=retries,
                backoff_seconds=backoff_seconds,
                timeout_seconds=timeout_seconds,
            )
        return dict(parse_playbyplay(data))

    @classmethod
    def get_match_shot_chart(
        cls,
        match_id: str,
        retries: int = 3,
        backoff_seconds: float = 0.6,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """
        Fetch shot chart coordinates from Genius Sports live data.json feed.

        Args:
            match_id: The match identifier from Genius Sports

        Returns:
            Dictionary containing shot list with x/y coordinates and metadata
        """
        if is_sportradar_id(match_id):
            from .pipeline.games import legacy_components
            from .pipeline.planning import plan_requests, resolve_reference
            from .pipeline.transport import RequestsTransport, RetryPolicy
            from .pipeline.types import Component

            reference = resolve_reference(match_id)
            request = plan_requests(reference, (Component.SHOTS,))[0]
            result = RequestsTransport(
                requests, RetryPolicy(retries, timeout_seconds, backoff_seconds)
            ).fetch(request)
            if result.failure is not None:
                raise ValueError(result.failure.message)
            return dict(fields(legacy_components(reference, (result,))["shot_chart"]))
        return cls._parse_match_shot_chart(
            match_id,
            cls.fetch_match_data_json(
                match_id, retries, backoff_seconds, timeout_seconds
            ),
        )

    @classmethod
    def _parse_match_shot_chart(
        cls, match_id: str, live_data: dict[str, Any]
    ) -> dict[str, Any]:
        from .genius_livestats_parser import parse_shot_chart

        return dict(parse_shot_chart(match_id, live_data))

    @classmethod
    def get_genius_teams(cls, competition_id: str) -> list[dict[str, Any]]:
        """
        Fetch teams from Genius Sports teams page for a specific competition.

        Args:
            competition_id: The Genius Sports competition identifier

        Returns:
            List of dictionaries containing team data (id, name)
        """
        url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/teams"
        response = requests.get(url, timeout=30.0)
        response.raise_for_status()

        return GeniusSportsParser.parse_teams_page(response.text)

    @classmethod
    def get_genius_players(
        cls, competition_id: str, output_file: str | None = None
    ) -> dict[str, Any]:
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
        response = requests.get(players_url, timeout=30.0)
        response.raise_for_status()

        # Parse player links from the page
        player_links_html = GeniusSportsParser.parse_players_page(response.text)
        print(f"Found {len(player_links_html)} players")

        result: dict[str, Any] = {
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
                gamelog_response = requests.get(gamelog_url, timeout=30.0)
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
                logging.getLogger(__name__).debug(
                    "genius_api: operation failed", exc_info=True
                )
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
        output_file: str | None = None,
    ) -> dict[str, Any]:
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
        response = requests.get(team_url, timeout=30.0)
        response.raise_for_status()

        # Parse player links from the roster page
        unique_players = GeniusSportsParser.parse_team_roster_page(response.text)
        print(f"Found {len(unique_players)} players on roster")

        # Print the player names first
        print(f"\n{team_name} roster:")
        for idx, player in enumerate(unique_players, 1):
            print(f"  {idx}. {player['name']} (ID: {player['id']})")
        print()

        result: dict[str, Any] = {
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
                gamelog_response = requests.get(gamelog_url, timeout=30.0)
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
                logging.getLogger(__name__).debug(
                    "genius_api: operation failed", exc_info=True
                )
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
        output_file: str | None = None,
    ) -> dict[str, Any]:
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
        response = requests.get(url, timeout=30.0)
        response.raise_for_status()

        # Parse the statistics
        stats = GeniusSportsParser.parse_team_statistics_page(response.text)

        result: dict[str, Any] = {
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
