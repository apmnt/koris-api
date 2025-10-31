"""Basket.fi/Torneopal API client for basic match and team data."""

import requests
from typing import Dict, Any, Optional, cast
import time


class BasketFiAPI:
    """Client for interacting with the Basket.fi/Torneopal API."""

    BASE_URL = "https://koripallo-api.torneopal.net/taso/rest"
    HEADERS = {
        "Accept": "json/df8e84j9xtdz269euy3h",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://tulospalvelu.basket.fi",
        "Priority": "u=3, i",
        "Referer": "https://tulospalvelu.basket.fi/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
    }

    @classmethod
    def get_matches(
        cls,
        competition_id: Optional[str] = None,
        category_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch matches for a specific competition and category, or for a specific team."""
        url = f"{cls.BASE_URL}/getMatches"
        params = {}

        if team_id:
            params["team_id"] = team_id
        elif competition_id and category_id:
            params["competition_id"] = competition_id
            params["category_id"] = category_id
        else:
            raise ValueError(
                "Either team_id or both competition_id and category_id must be provided"
            )

        start_time = time.time()
        response = requests.get(url, params=params, headers=cls.HEADERS)
        elapsed_time = time.time() - start_time

        # Raise an error for bad status codes
        response.raise_for_status()

        data = cast(Dict[str, Any], response.json())
        data["_fetch_time"] = elapsed_time
        data["_status_code"] = response.status_code
        return data

    @classmethod
    def get_match(cls, match_id: str) -> Dict[str, Any]:
        """
        Fetch detailed information for a specific match.

        Args:
            match_id: The match identifier

        Returns:
            Dictionary containing detailed match data including lineups and stats
        """
        url = f"{cls.BASE_URL}/getMatch"
        timestamp = str(int(time.time() * 1000))
        querystring = {"match_id": match_id, "timeStamp": timestamp}
        response = requests.get(url, headers=cls.HEADERS, params=querystring)
        response.raise_for_status()
        return cast(Dict[str, Any], response.json())

    @classmethod
    def get_team(cls, team_id: str) -> Dict[str, Any]:
        """
        Fetch team data including roster and officials.

        Args:
            team_id: The team identifier

        Returns:
            Dictionary containing team data
        """
        url = f"{cls.BASE_URL}/getTeam"
        querystring = {"team_id": team_id}
        response = requests.get(url, headers=cls.HEADERS, params=querystring)
        response.raise_for_status()
        return cast(Dict[str, Any], response.json())

    @classmethod
    def get_category(cls, competition_id: str, category_id: str) -> Dict[str, Any]:
        """
        Fetch category data including available seasons.

        Args:
            competition_id: The competition identifier
            category_id: The category identifier

        Returns:
            Dictionary containing category data
        """
        url = f"{cls.BASE_URL}/getCategory"
        querystring = {"competition_id": competition_id, "category_id": category_id}
        response = requests.get(url, headers=cls.HEADERS, params=querystring)
        response.raise_for_status()
        return cast(Dict[str, Any], response.json())
