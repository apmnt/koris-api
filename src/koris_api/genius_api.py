"""Genius Sports HTML scraping client for advanced box scores and player data."""

import requests
from typing import Dict, Any, Optional, List, cast
import time
import json
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag
import re
from .genius_parser import GeniusSportsParser


class GeniusSportsAPI:
    """Client for scraping basketball data from Genius Sports hosted pages."""

    @classmethod
    def get_match_boxscore(cls, match_id: str) -> Dict[str, Any]:
        """
        Fetch and parse box score data from the Genius Sports hosted page.

        Args:
            match_id: The match identifier from Genius Sports

        Returns:
            Dictionary containing parsed box score data with team stats and player stats
        """
        url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/match/{match_id}/boxscore"
        response = requests.get(url)
        response.raise_for_status()

        return GeniusSportsParser.parse_boxscore_html(response.text)

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
