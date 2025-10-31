"""BasketHotel API client for fetching older basketball game data."""

import requests
from typing import Dict, Any
import re
from urllib.parse import urlencode
from .baskethotel_parser import BasketHotelParser


class BasketHotelAPI:
    """Client for fetching basketball game data from BasketHotel widget API (for older games)."""

    def __init__(self):
        self.base_url = "https://widgets.baskethotel.com/widget-service/show"
        self.api_key = "b9680714b4026e011e13a43ccb7dfa201932958c"  # basket.fi API key

    def fetch_game_data(
        self, game_id: str, season_id: str = "121333", league_id: str = "2"
    ) -> Dict[str, Any]:
        """
        Fetch complete game data from BasketHotel API

        Args:
            game_id: Unique game identifier
            season_id: Season identifier (default: 121333)
            league_id: League identifier (default: 2)

        Returns:
            Dictionary containing structured game data
        """
        # First, get the widget state
        url = self._build_game_url(game_id, season_id, league_id)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://www.basket.fi/",
        }

        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # Extract the state token from the response (needed for subsequent requests)
        # The state is in JavaScript code, so quotes are escaped
        state_match = re.search(r"state:\s*\\'([^\\]+)\\'", response.text)
        if not state_match:
            # Try to parse what we got anyway
            html_content = BasketHotelParser.extract_html_from_response(response.text)
            game_data = BasketHotelParser.parse_game_html(html_content)
            return game_data

        # First, extract team names from the initial response
        initial_html = BasketHotelParser.extract_html_from_response(response.text)
        initial_data = BasketHotelParser.parse_game_html(initial_html)

        state = state_match.group(1)

        # Now fetch the actual game data using the "home" part
        game_data_url = self._build_game_part_url(game_id, state)

        response2 = requests.get(game_data_url, headers=headers)
        response2.raise_for_status()

        # Extract HTML from JavaScript response
        html_content = BasketHotelParser.extract_html_from_response(response2.text)

        # Parse HTML to extract structured data
        game_data = BasketHotelParser.parse_game_html(html_content)

        # Merge team names from initial response
        if initial_data.get("teams", {}).get("home", {}).get("name"):
            game_data["teams"]["home"]["name"] = initial_data["teams"]["home"]["name"]
        if initial_data.get("teams", {}).get("away", {}).get("name"):
            game_data["teams"]["away"]["name"] = initial_data["teams"]["away"]["name"]

        # Merge game_info from initial response (date, time, venue, attendance, game_id)
        if initial_data.get("game_info"):
            game_data["game_info"].update(initial_data["game_info"])

        return game_data

    def _build_game_url(self, game_id: str, season_id: str, league_id: str) -> str:
        """Build the API URL with all required parameters"""
        params = {
            "api": self.api_key,
            "lang": "fi",
            "nnav": "1",
            "nav_object": "0",
            "hide_full_birth_date": "1",
            "flash": "0",
            # Widget 400 - Game Full View
            "request[0][container]": "view4",
            "request[0][widget]": "400",
            "request[0][param][game_id]": game_id,
            "request[0][param][season_id]": season_id,
            "request[0][param][league_id]": league_id,
            "request[0][param][template]": "v2",
            # Widget 402 - Extra Stats
            "request[1][container]": "view5",
            "request[1][widget]": "402",
            "request[1][param][game_id]": game_id,
            "request[1][param][season_id]": season_id,
            "request[1][param][league_id]": "4",
            "request[1][param][template]": "v2",
        }

        return f"{self.base_url}?{urlencode(params)}"

    def _build_game_part_url(self, game_id: str, state: str) -> str:
        """Build URL to fetch specific game data part"""
        params = {
            "api": self.api_key,
            "lang": "fi",
            "nnav": "1",
            "nav_object": "0",
            "hide_full_birth_date": "1",
            "flash": "0",
            "request[0][container]": "2-400-tab-container",
            "request[0][widget]": "400",
            "request[0][part]": "home",  # This loads the actual game data
            "request[0][state]": state,
            "request[0][param][game_id]": game_id,
        }

        return f"{self.base_url}?{urlencode(params)}"
