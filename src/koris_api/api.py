import requests
from typing import Dict, Any, Optional, cast, List
import time
import json
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag
import re


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

        soup = BeautifulSoup(response.text, "lxml")
        teams = []
        seen_ids = set()

        # Find all links with /team/ in href
        all_links = soup.find_all("a", href=re.compile(r"/team/\d+"))
        for link in all_links:
            href = link.get("href", "")
            # Extract team ID from href
            # Format: /FBAA/en/competition/42145/team/98486?
            match = re.search(r"/team/(\d+)", str(href))
            if match:
                team_id = match.group(1)
                team_name = link.get_text(strip=True)

                # Avoid duplicates and empty names
                if team_id not in seen_ids and team_name:
                    seen_ids.add(team_id)
                    teams.append({"id": team_id, "name": team_name})

        return teams

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

        soup = BeautifulSoup(response.text, "lxml")

        # Find all player links
        player_links = soup.find_all("a", class_="playername")
        print(f"Found {len(player_links)} players")

        result: Dict[str, Any] = {
            "competition_id": competition_id,
            "teams": teams,
            "players": [],
        }

        # Process each player
        for idx, link in enumerate(player_links, 1):
            href = link.get("href", "")
            player_name = link.get_text(strip=True)

            # Extract player ID from href
            # Format: /FBAA/en/competition/42145/person/457315?
            match = re.search(r"/person/(\d+)", href)
            if not match:
                continue

            player_id = match.group(1)
            print(f"Processing player {idx}/{len(player_links)}: {player_name}")

            # Fetch player's gamelog
            gamelog_url = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{competition_id}/person/{player_id}/gamelog"

            try:
                gamelog_response = requests.get(gamelog_url)
                gamelog_response.raise_for_status()

                # Parse gamelog
                gamelog_data = cls._parse_player_gamelog(
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

        soup = BeautifulSoup(response.text, "lxml")

        # Find all player links on the team page
        # They should be in links that go to /person/
        player_links = []
        for link in soup.find_all("a", href=re.compile(r"/person/\d+")):
            # Only get unique player links
            href = link.get("href", "")
            match = re.search(r"/person/(\d+)", str(href))
            if match:
                player_id = match.group(1)
                player_name = link.get_text(strip=True)
                if player_name:  # Only add if there's a name
                    player_links.append(
                        {"id": player_id, "name": player_name, "href": href}
                    )

        # Remove duplicates based on player ID
        seen_ids = set()
        unique_players = []
        for player in player_links:
            if player["id"] not in seen_ids:
                seen_ids.add(player["id"])
                unique_players.append(player)

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
                time.sleep(0.5)  # Be nice to the server
                gamelog_response = requests.get(gamelog_url)
                gamelog_response.raise_for_status()

                # Parse gamelog
                gamelog_data = cls._parse_player_gamelog(
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
    def _parse_player_gamelog(
        cls, html_content: str, teams_dict: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Parse player gamelog HTML content.

        Args:
            html_content: HTML content from the gamelog page
            teams_dict: Dictionary mapping team IDs to team names

        Returns:
            Dictionary containing player's team and game statistics
        """
        soup = BeautifulSoup(html_content, "lxml")

        result: Dict[str, Any] = {
            "team": None,
            "team_id": None,
            "games": [],
        }

        # Find the table with game logs
        table = soup.find("table", class_="tableClass")
        if not table or isinstance(table, (NavigableString, int)):
            return result

        # Get column headers
        headers = []
        thead = table.find("thead")
        if thead and isinstance(thead, Tag):
            header_row = thead.find("tr")
            if header_row and isinstance(header_row, Tag):
                for th in header_row.find_all("th"):
                    title = th.get("title", th.get_text(strip=True))
                    headers.append(title)

        # Get game stats - try tbody first, then fall back to all tr elements
        tbody = table.find("tbody")
        rows: List[Any] = []
        if tbody and isinstance(tbody, Tag):
            rows = tbody.find_all("tr")
        else:
            # No tbody, get all tr elements and filter out the header row
            all_rows = table.find_all("tr")
            for r in all_rows:
                if r.find_parent("thead") is None:
                    rows.append(r)

        for row in rows:
            if not isinstance(row, Tag):
                continue

            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            game_stat: Dict[str, Any] = {}

            for i, cell in enumerate(cells):
                if i >= len(headers):
                    break

                header = headers[i]

                # Handle Team column - extract team name and ID
                if header == "Team":
                    link = cell.find("a")
                    if link and isinstance(link, Tag):
                        team_name = link.get_text(strip=True)
                        href = link.get("href", "")
                        # Extract team ID from href
                        team_match = re.search(r"/team/(\d+)", str(href))
                        if team_match:
                            team_id = team_match.group(1)
                            # Set player's team from first game
                            if result["team"] is None:
                                result["team"] = team_name
                                result["team_id"] = team_id
                        game_stat["Team"] = team_name
                    continue

                # Handle Date column - extract date and match link
                if header == "Date":
                    link = cell.find("a")
                    if link and isinstance(link, Tag):
                        date_text = link.get_text(strip=True)
                        href = link.get("href", "")
                        # Extract match ID from href
                        match_match = re.search(r"/match/(\d+)", str(href))
                        if match_match:
                            game_stat["Match ID"] = match_match.group(1)
                        game_stat[header] = date_text
                    else:
                        game_stat[header] = cell.get_text(strip=True)
                    continue

                # Get value from cell
                value = cell.get_text(strip=True)

                # Try to convert to appropriate type for numeric fields
                if header not in ["Team", "Date"]:
                    try:
                        # Check if it's a time format (MM:SS)
                        if ":" in value:
                            value = value  # Keep as string for time
                        elif "." in value:
                            value = float(value)
                        else:
                            value = int(value)
                    except ValueError:
                        pass  # Keep as string

                game_stat[header] = value

            if game_stat:
                result["games"].append(game_stat)

        return result
