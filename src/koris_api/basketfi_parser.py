"""Parser for BasketFi API JSON responses."""

from collections.abc import Mapping, Sequence

from .data import fields, records, text


class BasketFiParser:
    """Parser for BasketFi API JSON responses."""

    @staticmethod
    def extract_category_name(category_data: Mapping[str, object]) -> str:
        """
        Extract category name from category API response.

        Args:
            category_data: Category API response dictionary

        Returns:
            Category name, or "Unknown" if not found
        """
        if "category" in category_data:
            return (
                text(fields(category_data["category"]).get("category_name"))
                or "Unknown"
            )
        return "Unknown"

    @staticmethod
    def extract_team_name(team_data: Mapping[str, object]) -> str:
        """
        Extract team name from team API response.

        Args:
            team_data: Team API response dictionary

        Returns:
            Team name, or "Unknown" if not found
        """
        if "team" in team_data:
            return text(fields(team_data["team"]).get("team_name")) or "Unknown"
        return "Unknown"

    @staticmethod
    def extract_matches(matches_data: Mapping[str, object]) -> list[dict[str, object]]:
        """
        Extract matches list from matches API response.

        Args:
            matches_data: Matches API response dictionary

        Returns:
            List of match dictionaries, empty list if not found
        """
        matches = matches_data.get("matches", [])
        if not isinstance(matches, list):
            return []
        result: list[dict[str, object]] = []
        for raw_match in records(matches_data.get("matches")):
            match = dict(raw_match)
            status = match.get("status")
            if status not in {"Played", "Fixture"}:
                home_score = match.get("fs_A")
                status = "Played" if home_score not in (None, "") else "Fixture"
                match["status"] = status
            result.append(match)
        return result

    @staticmethod
    def is_match_played(match: Mapping[str, object]) -> bool:
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
    def parse_match(
        match: Mapping[str, object], season_name: str | None = None
    ) -> dict[str, object]:
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

        status = match.get("status")
        if status not in {"Played", "Fixture"}:
            status = "Played" if home_score not in (None, "") else "Fixture"

        match_data = {
            "match_id": match.get("match_id"),
            "match_external_id": match.get("match_external_id"),
            "category_external_id": match.get("category_external_id"),
            "date": match.get("date"),
            "time": match.get("time"),
            "home_team": match.get("team_A_name") or match.get("club_A_name"),
            "home_team_id": match.get("team_A_id"),
            "away_team": match.get("team_B_name") or match.get("club_B_name"),
            "away_team_id": match.get("team_B_id"),
            "home_score": home_score,
            "away_score": away_score,
            "status": status,
            "venue": match.get("venue_name"),
            "competition": match.get("competition_name"),
            "category": match.get("category_name"),
            "season": season_name
            or match.get("season_id")
            or match.get("competition_id"),
        }

        return match_data

    @staticmethod
    def parse_matches(
        matches: Sequence[Mapping[str, object]],
        season_name: str | None = None,
        only_played: bool = True,
    ) -> list[dict[str, object]]:
        """
        Parse a list of matches from BasketFi API format.

        Args:
            matches: List of raw match dictionaries from API
            season_name: Optional season name to include in parsed matches
            only_played: Whether to only include played matches

        Returns:
            List of parsed match dictionaries
        """
        processed_matches: list[dict[str, object]] = []

        for match in matches:
            # Check if match has been played (has scores)
            if only_played and not BasketFiParser.is_match_played(match):
                continue

            match_data = BasketFiParser.parse_match(match, season_name)
            processed_matches.append(match_data)

        return processed_matches

    @staticmethod
    def filter_matches_by_season(
        matches: Sequence[Mapping[str, object]],
        competition_id: str,
        category_id: str | None = None,
    ) -> list[dict[str, object]]:
        """
        Filter matches by competition/season ID and optionally category ID.

        Args:
            matches: List of match dictionaries
            competition_id: Competition/season identifier to filter by
            category_id: Optional category identifier to filter by

        Returns:
            Filtered list of matches
        """
        filtered: list[dict[str, object]] = []
        for match in matches:
            # Check if match belongs to the requested competition/season
            match_comp_id = match.get("competition_id")
            match_category_id = match.get("category_id")
            if match_comp_id == competition_id and (
                not category_id or match_category_id == category_id
            ):
                filtered.append(dict(match))
        return filtered

    @staticmethod
    def extract_teams_from_matches(
        matches: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """
        Extract unique teams from a list of matches.

        Args:
            matches: List of parsed match dictionaries

        Returns:
            List of unique team dictionaries with team_id and team_name
        """
        teams: dict[str, dict[str, object]] = {}
        for match in matches:
            for side in ("home", "away"):
                team_id = match.get(f"{side}_team_id")
                name = match.get(f"{side}_team")
                if team_id and name:
                    _ = teams.setdefault(
                        str(team_id), {"team_id": team_id, "team_name": name}
                    )
        return list(teams.values())

    @staticmethod
    def extract_category_external_id(category_data: Mapping[str, object]) -> str | None:
        """
        Extract category_external_id from category API response.

        Args:
            category_data: Category API response dictionary

        Returns:
            External ID string if found, None otherwise
        """
        if "category" in category_data:
            external_id = text(
                fields(category_data["category"]).get("category_external_id")
            )
            if external_id and external_id.strip():
                return external_id.strip()
        return None
