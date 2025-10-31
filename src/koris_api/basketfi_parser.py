"""Parser for BasketFi API JSON responses."""

from typing import Dict, Any, List, Optional


class BasketFiParser:
    """Parser for BasketFi API JSON responses."""

    @staticmethod
    def extract_category_name(category_data: Dict[str, Any]) -> str:
        """
        Extract category name from category API response.

        Args:
            category_data: Category API response dictionary

        Returns:
            Category name, or "Unknown" if not found
        """
        if "category" in category_data:
            return category_data["category"].get("category_name", "Unknown")
        return "Unknown"

    @staticmethod
    def extract_team_name(team_data: Dict[str, Any]) -> str:
        """
        Extract team name from team API response.

        Args:
            team_data: Team API response dictionary

        Returns:
            Team name, or "Unknown" if not found
        """
        if "team" in team_data:
            return team_data["team"].get("team_name", "Unknown")
        return "Unknown"

    @staticmethod
    def extract_matches(matches_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract matches list from matches API response.

        Args:
            matches_data: Matches API response dictionary

        Returns:
            List of match dictionaries, empty list if not found
        """
        return matches_data.get("matches", [])

    @staticmethod
    def is_match_played(match: Dict[str, Any]) -> bool:
        """
        Check if a match has been played (has scores).

        Args:
            match: Match dictionary from API

        Returns:
            True if match has been played, False otherwise
        """
        home_score = match.get("fs_A")
        away_score = match.get("fs_B")
        return (
            home_score is not None
            and away_score is not None
            and home_score != ""
            and away_score != ""
        )

    @staticmethod
    def parse_match(match: Dict[str, Any], season_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse and transform a match from BasketFi API format to a standardized format.

        Args:
            match: Raw match dictionary from API
            season_name: Optional season name to include in the parsed match

        Returns:
            Parsed match dictionary with standardized field names
        """
        home_score = match.get("fs_A")
        away_score = match.get("fs_B")

        match_data = {
            "match_id": match.get("match_id"),
            "match_external_id": match.get("match_external_id"),
            "date": match.get("date"),
            "time": match.get("time"),
            "home_team": match.get("club_A_name"),
            "home_team_id": match.get("team_A_id"),
            "away_team": match.get("club_B_name"),
            "away_team_id": match.get("team_B_id"),
            "home_score": home_score,
            "away_score": away_score,
            "status": match.get("status"),
            "venue": match.get("venue_name"),
            "competition": match.get("competition_name"),
            "category": match.get("category_name"),
            "season": season_name or match.get("season_id") or match.get("competition_id"),
        }

        return match_data

    @staticmethod
    def parse_matches(
        matches: List[Dict[str, Any]],
        season_name: Optional[str] = None,
        only_played: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Parse a list of matches from BasketFi API format.

        Args:
            matches: List of raw match dictionaries from API
            season_name: Optional season name to include in parsed matches
            only_played: Whether to only include played matches

        Returns:
            List of parsed match dictionaries
        """
        processed_matches = []

        for match in matches:
            # Check if match has been played (has scores)
            if only_played and not BasketFiParser.is_match_played(match):
                continue

            match_data = BasketFiParser.parse_match(match, season_name)
            processed_matches.append(match_data)

        return processed_matches

    @staticmethod
    def filter_matches_by_season(
        matches: List[Dict[str, Any]],
        competition_id: str,
        category_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter matches by competition/season ID and optionally category ID.

        Args:
            matches: List of match dictionaries
            competition_id: Competition/season identifier to filter by
            category_id: Optional category identifier to filter by

        Returns:
            Filtered list of matches
        """
        filtered = []
        for match in matches:
            # Check if match belongs to the requested competition/season
            match_comp_id = match.get("competition_id")
            match_category_id = match.get("category_id")
            if match_comp_id == competition_id and (
                not category_id or match_category_id == category_id
            ):
                filtered.append(match)
        return filtered

    @staticmethod
    def extract_teams_from_matches(
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Extract unique teams from a list of matches.

        Args:
            matches: List of parsed match dictionaries

        Returns:
            List of unique team dictionaries with team_id and team_name
        """
        teams_dict: Dict[str, Dict[str, Any]] = {}
        for match in matches:
            home_id = match.get("home_team_id")
            home_name = match.get("home_team")
            away_id = match.get("away_team_id")
            away_name = match.get("away_team")

            if home_id and home_name:
                if home_id not in teams_dict:
                    teams_dict[home_id] = {
                        "team_id": home_id,
                        "team_name": home_name,
                    }

            if away_id and away_name:
                if away_id not in teams_dict:
                    teams_dict[away_id] = {
                        "team_id": away_id,
                        "team_name": away_name,
                    }

        return list(teams_dict.values())

    @staticmethod
    def extract_category_external_id(category_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract category_external_id from category API response.

        Args:
            category_data: Category API response dictionary

        Returns:
            External ID string if found, None otherwise
        """
        if "category" in category_data:
            external_id = category_data["category"].get("category_external_id")
            if external_id and external_id.strip():
                return external_id.strip()
        return None
