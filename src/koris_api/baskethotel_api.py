"""BasketHotel API client for fetching older basketball game data."""

import requests
import httpx
from typing import Dict, Any
import re
from urllib.parse import urlencode
from .baskethotel_parser import BasketHotelParser
from bs4 import BeautifulSoup, Tag


class BasketHotelAPI:
    """Client for fetching basketball game data from BasketHotel widget API (for older games)."""

    def __init__(self):
        self.base_url = "https://widgets.baskethotel.com/widget-service/show"
        self.api_key = "b9680714b4026e011e13a43ccb7dfa201932958c"  # basket.fi API key

    def fetch_game_data(
        self,
        game_id: str,
        season_id: str = "121333",
        league_id: str = "2",
        session: requests.Session | None = None,
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

        client = session or requests
        response = client.get(url, headers=headers)
        response.raise_for_status()

        # Extract the state token from the response (needed for subsequent requests)
        # The state is in JavaScript code, so quotes are escaped
        state = self._extract_state(response.text)
        if not state:
            # Try to parse what we got anyway
            html_content = BasketHotelParser.extract_html_from_response(response.text)
            game_data = BasketHotelParser.parse_game_html(html_content)
            return game_data

        # First, extract team names from the initial response
        initial_html = BasketHotelParser.extract_html_from_response(response.text)
        initial_data = BasketHotelParser.parse_game_html(initial_html)

        # Now fetch the actual game data using the "home" part
        game_data_url = self._build_game_part_url(game_id, state)

        response2 = client.get(game_data_url, headers=headers)
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

    def _build_game_part_url(
        self,
        game_id: str,
        state: str,
        part: str = "home",
        container: str = "2-400-tab-container",
        extra_params: Dict[str, Any] | None = None,
    ) -> str:
        """Build URL to fetch specific game data part"""
        params = {
            "api": self.api_key,
            "lang": "fi",
            "nnav": "1",
            "nav_object": "0",
            "hide_full_birth_date": "1",
            "flash": "0",
            "request[0][container]": container,
            "request[0][widget]": "400",
            "request[0][part]": part,
            "request[0][state]": state,
            "request[0][param][game_id]": game_id,
        }
        if extra_params:
            params.update(extra_params)

        return f"{self.base_url}?{urlencode(params)}"

    def _extract_state(self, response_text: str) -> str | None:
        state_match = re.search(r"state:\s*\\\\'([^\\\\]+)\\\\'", response_text)
        if state_match:
            return state_match.group(1)
        try:
            html = BasketHotelParser.extract_html_from_response(response_text)
        except Exception:
            return None
        state_match = re.search(r"state:\s*'([^']+)'", html)
        if state_match:
            return state_match.group(1)
        return None

    def fetch_boxscore_data(
        self,
        game_id: str,
        season_id: str = "121333",
        league_id: str = "2",
        session: requests.Session | None = None,
    ) -> Dict[str, Any]:
        """
        Fetch boxscore data for a single game from BasketHotel API.

        Args:
            game_id: Unique game identifier
            season_id: Season identifier
            league_id: League identifier

        Returns:
            Dictionary containing parsed team totals for the game.
        """
        url = self._build_game_url(game_id, season_id, league_id)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://www.basket.fi/",
        }

        client = session or requests
        response = client.get(url, headers=headers)
        response.raise_for_status()
        initial_html = BasketHotelParser.extract_html_from_response(response.text)
        initial_data = BasketHotelParser.parse_game_html(initial_html)

        state = self._extract_state(response.text)
        if not state:
            boxscore = BasketHotelParser.parse_boxscore_html(initial_html)
            boxscore["game_info"] = initial_data.get("game_info", {})
            boxscore["game_teams"] = initial_data.get("teams", {})
            return boxscore

        game_data_url = self._build_game_part_url(game_id, state, part="boxscore")
        response2 = client.get(game_data_url, headers=headers)
        response2.raise_for_status()
        html_content = BasketHotelParser.extract_html_from_response(response2.text)
        boxscore = BasketHotelParser.parse_boxscore_html(html_content)
        boxscore["game_info"] = initial_data.get("game_info", {})
        boxscore["game_teams"] = initial_data.get("teams", {})
        return boxscore

    async def fetch_boxscore_data_async(
        self,
        game_id: str,
        season_id: str = "121333",
        league_id: str = "2",
        client: httpx.AsyncClient | None = None,
    ) -> Dict[str, Any]:
        """
        Fetch boxscore data for a single game from BasketHotel API asynchronously.

        Args:
            game_id: Unique game identifier
            season_id: Season identifier
            league_id: League identifier
            client: Optional shared async client

        Returns:
            Dictionary containing parsed team totals for the game.
        """
        url = self._build_game_url(game_id, season_id, league_id)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://www.basket.fi/",
        }

        if client is None:
            async with httpx.AsyncClient(headers=headers) as session:
                return await self.fetch_boxscore_data_async(
                    game_id, season_id, league_id, client=session
                )

        response = await client.get(url, headers=headers)
        response.raise_for_status()
        initial_html = BasketHotelParser.extract_html_from_response(response.text)
        initial_data = BasketHotelParser.parse_game_html(initial_html)

        state = self._extract_state(response.text)
        if not state:
            boxscore = BasketHotelParser.parse_boxscore_html(initial_html)
            boxscore["game_info"] = initial_data.get("game_info", {})
            boxscore["game_teams"] = initial_data.get("teams", {})
            return boxscore

        game_data_url = self._build_game_part_url(game_id, state, part="boxscore")
        response2 = await client.get(game_data_url, headers=headers)
        response2.raise_for_status()
        html_content = BasketHotelParser.extract_html_from_response(response2.text)
        boxscore = BasketHotelParser.parse_boxscore_html(html_content)
        boxscore["game_info"] = initial_data.get("game_info", {})
        boxscore["game_teams"] = initial_data.get("teams", {})
        return boxscore

    def fetch_playbyplay_data(
        self,
        game_id: str,
        season_id: str = "121333",
        league_id: str = "2",
        session: requests.Session | None = None,
    ) -> Dict[str, Any]:
        """
        Fetch play-by-play data for a single game from BasketHotel API.

        Args:
            game_id: Unique game identifier
            season_id: Season identifier
            league_id: League identifier

        Returns:
            Dictionary containing parsed play-by-play events.
        """
        url = self._build_game_url(game_id, season_id, league_id)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://www.basket.fi/",
        }

        client = session or requests
        response = client.get(url, headers=headers)
        response.raise_for_status()

        initial_html = BasketHotelParser.extract_html_from_response(response.text)
        initial_data = BasketHotelParser.parse_game_html(initial_html)
        playbyplay = BasketHotelParser.parse_playbyplay_html(initial_html)

        state = self._extract_state(response.text)
        if state:
            # Load the play-by-play tab to get filters + a dedicated state token.
            tab_params = {
                "request[0][param][season_id]": season_id,
                "request[0][param][league_id]": league_id,
                "request[0][param][template]": "v2",
            }
            tab_url = self._build_game_part_url(
                game_id,
                state,
                part="play-by-play",
                container="2-400-tab-container",
                extra_params=tab_params,
            )
            response_tab = client.get(tab_url, headers=headers)
            response_tab.raise_for_status()
            tab_html = BasketHotelParser.extract_html_from_response(response_tab.text)

            # Extract filter values that are selected by default.
            filters = {"quarter": [], "player_a": [], "player_b": [], "action": []}
            soup = BeautifulSoup(tab_html, "html.parser")
            for li in soup.select("li[data-filter]"):
                if not isinstance(li, Tag):
                    continue
                filter_name = li.get("data-filter")
                if filter_name not in filters:
                    continue
                if li.get("data-filter-selected") == "true":
                    value = li.get("data-filter-value")
                    if value:
                        filters[filter_name].append(value)

            playbyplay_state = self._extract_state(tab_html)
            if playbyplay_state:
                action_params = {
                    "request[0][param][season_id]": season_id,
                    "request[0][param][filter][quarter]": ",".join(
                        filters["quarter"]
                    ),
                    "request[0][param][filter][player_a]": ",".join(
                        filters["player_a"]
                    ),
                    "request[0][param][filter][player_b]": ",".join(
                        filters["player_b"]
                    ),
                    "request[0][param][filter][action]": ",".join(
                        filters["action"]
                    ),
                }
                actions_url = self._build_game_part_url(
                    game_id,
                    playbyplay_state,
                    part="play-by-play-actions",
                    container="2-400-play-by-play-actions-container",
                    extra_params=action_params,
                )
                response2 = client.get(actions_url, headers=headers)
                response2.raise_for_status()
                html_content = BasketHotelParser.extract_html_from_response(
                    response2.text
                )
                parsed = BasketHotelParser.parse_playbyplay_html(html_content)
                if parsed.get("events"):
                    playbyplay = parsed

        playbyplay["game_info"] = initial_data.get("game_info", {})
        playbyplay["game_teams"] = initial_data.get("teams", {})
        playbyplay["score"] = initial_data.get("score", {})

        team_map = {}
        home_name = playbyplay.get("game_teams", {}).get("home", {}).get("name")
        away_name = playbyplay.get("game_teams", {}).get("away", {}).get("name")
        if home_name:
            team_map[home_name] = "1"
        if away_name:
            team_map[away_name] = "2"

        normalized = BasketHotelParser.normalize_playbyplay_to_genius_format(
            playbyplay, team_map=team_map
        )
        normalized["source"] = "baskethotel"
        return normalized
