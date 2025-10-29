import requests
from typing import Dict, Any, Optional, cast
import time
from bs4 import BeautifulSoup, NavigableString, Tag


class KorisAPI:
    """Client for interacting with the Koripallo API."""

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

        return cls._parse_boxscore_html(response.text)

    @classmethod
    def _parse_boxscore_html(cls, html_content: str) -> Dict[str, Any]:
        """
        Parse box score HTML content and extract player and team statistics.

        Args:
            html_content: HTML content from the box score page

        Returns:
            Dictionary containing parsed box score data
        """
        soup = BeautifulSoup(html_content, "lxml")

        result: Dict[str, Any] = {"match_info": {}, "teams": []}

        # Extract match info from header
        match_header: Optional[NavigableString | Tag] = soup.find(
            "div", class_="match-header"
        )

        if match_header is None or isinstance(match_header, NavigableString):
            raise ValueError("Match header not found in box score HTML")

        home_wrapper = match_header.find("div", class_="home-wrapper")
        away_wrapper = match_header.find("div", class_="away-wrapper")

        if home_wrapper and isinstance(home_wrapper, Tag):
            home_name_elem = home_wrapper.find("span", class_="name")
            home_score_elem = home_wrapper.find("div", class_="score")
            result["match_info"]["home_team"] = (
                home_name_elem.get_text(strip=True) if home_name_elem else None
            )
            result["match_info"]["home_score"] = (
                int(home_score_elem.get_text(strip=True)) if home_score_elem else None
            )

        if away_wrapper and isinstance(away_wrapper, Tag):
            away_name_elem = away_wrapper.find("span", class_="name")
            away_score_elem = away_wrapper.find("div", class_="score")
            result["match_info"]["away_team"] = (
                away_name_elem.get_text(strip=True) if away_name_elem else None
            )
            result["match_info"]["away_score"] = (
                int(away_score_elem.get_text(strip=True)) if away_score_elem else None
            )

        # Get match status
        status_elem = match_header.find("span", class_="status")
        if status_elem:
            result["match_info"]["status"] = status_elem.get_text(strip=True)

        # Get match details (date, venue)
        details = match_header.find("div", class_="details")
        if details and isinstance(details, Tag):
            time_elem = details.find("div", class_="match-time")
            venue_elem = details.find("div", class_="match-venue")
            if time_elem and isinstance(time_elem, Tag):
                time_span = time_elem.find("span")
                result["match_info"]["datetime"] = (
                    time_span.get_text(strip=True)
                    if time_span and isinstance(time_span, Tag)
                    else None
                )
            if venue_elem and isinstance(venue_elem, Tag):
                venue_span = venue_elem.find("span")
                result["match_info"]["venue"] = (
                    venue_span.get_text(strip=True)
                    if venue_span and isinstance(venue_span, Tag)
                    else None
                )

        # Extract team stats from tables
        tables = soup.find_all("table", class_="tableClass")

        for table in tables:
            team_data: Dict[str, Any] = {"players": [], "totals": {}, "coaches": {}}

            # Get team name from preceding h4
            h4 = table.find_previous("h4")
            if h4:
                team_data["team_name"] = h4.get_text(strip=True)

            # Get column headers
            headers = []
            thead = table.find("thead")
            if thead:
                header_row = thead.find("tr")
                if header_row:
                    for th in header_row.find_all("th"):
                        title = th.get("title", th.get_text(strip=True))
                        headers.append(title)

            # Get player stats
            tbody = table.find("tbody")
            if tbody:
                for row in tbody.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) < len(headers):
                        continue

                    player_stat: Dict[str, Any] = {}
                    for i, cell in enumerate(cells):
                        if i >= len(headers):
                            break

                        header = headers[i]
                        # Get the data-sort-value if available, otherwise text
                        value = cell.get("data-sort-value")
                        if value is None:
                            # For player name and number, get text
                            if "Player" in header:
                                link = cell.find("a")
                                value = (
                                    link.get_text(strip=True)
                                    if link
                                    else cell.get_text(strip=True)
                                )
                            else:
                                value = cell.get_text(strip=True)
                                # Try to convert to number for numeric fields
                                if header == "Shirt Number" or header == "No":
                                    try:
                                        value = int(value)
                                    except ValueError:
                                        pass
                        else:
                            # Try to convert to appropriate type
                            try:
                                # Check if it's an integer
                                if "." not in value:
                                    value = int(value)
                                else:
                                    value = float(value)
                            except ValueError:
                                pass  # Keep as string

                        player_stat[header] = value

                    if player_stat:
                        team_data["players"].append(player_stat)

            # Get team totals
            tfoot = table.find("tfoot")
            if tfoot:
                total_row = tfoot.find("tr")
                if total_row:
                    cells = total_row.find_all("td")
                    for i, cell in enumerate(
                        cells[2:], start=2
                    ):  # Skip "Totals" and empty cells
                        if i >= len(headers):
                            break
                        header = headers[i]
                        value = cell.get_text(strip=True)
                        # Try to convert to number
                        try:
                            if "." in value:
                                value = float(value)
                            else:
                                value = int(value)
                        except ValueError:
                            pass
                        team_data["totals"][header] = value

            # Get coaches
            staff_div = table.find_next("div", class_="matchStaff")
            if staff_div:
                staff_text = staff_div.get_text()
                if "Head Coach:" in staff_text:
                    head_coach = (
                        staff_text.split("Head Coach:")[1].split("Coach:")[0].strip()
                    )
                    team_data["coaches"]["head_coach"] = head_coach
                if "Coach:" in staff_text:
                    assistant_coach = staff_text.split("Coach:")[-1].strip()
                    team_data["coaches"]["assistant_coach"] = assistant_coach

            if team_data["players"]:  # Only add if we found players
                result["teams"].append(team_data)

        return result
