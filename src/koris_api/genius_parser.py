"""Parser for Genius Sports HTML responses."""

from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup, NavigableString, Tag
import re


class GeniusSportsParser:
    """Parser for Genius Sports HTML content."""

    @staticmethod
    def _safe_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def parse_boxscore_html(html_content: str) -> Dict[str, Any]:
        """
        Parse box score HTML content and extract player and team statistics.

        Args:
            html_content: HTML content from the box score page

        Returns:
            Dictionary containing parsed box score data
        """
        soup = BeautifulSoup(html_content, "html.parser")

        result: Dict[str, Any] = {"match_info": {}, "teams": []}

        # Extract match info from header
        match_header: NavigableString | Tag | None = soup.find(
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
                GeniusSportsParser._safe_int(
                    home_score_elem.get_text(strip=True) if home_score_elem else None
                )
            )

        if away_wrapper and isinstance(away_wrapper, Tag):
            away_name_elem = away_wrapper.find("span", class_="name")
            away_score_elem = away_wrapper.find("div", class_="score")
            result["match_info"]["away_team"] = (
                away_name_elem.get_text(strip=True) if away_name_elem else None
            )
            result["match_info"]["away_score"] = (
                GeniusSportsParser._safe_int(
                    away_score_elem.get_text(strip=True) if away_score_elem else None
                )
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

    @staticmethod
    def parse_playbyplay_html(html_content: str) -> Dict[str, Any]:
        """
        Parse play-by-play HTML content and extract game events.

        Args:
            html_content: HTML content from the play-by-play page

        Returns:
            Dictionary containing parsed play-by-play data
        """
        soup = BeautifulSoup(html_content, "html.parser")

        result: Dict[str, Any] = {
            "match_info": {},
            "events": [],
            "players": [],
        }
        player_index: Dict[tuple, int] = {}

        # Extract match info from header
        match_header = soup.find("div", class_="match-header")

        if match_header and isinstance(match_header, Tag):
            home_wrapper = match_header.find("div", class_="home-wrapper")
            away_wrapper = match_header.find("div", class_="away-wrapper")

            if home_wrapper and isinstance(home_wrapper, Tag):
                home_name_elem = home_wrapper.find("span", class_="name")
                home_score_elem = home_wrapper.find("div", class_="score")
                result["match_info"]["home_team"] = (
                    home_name_elem.get_text(strip=True) if home_name_elem else None
                )
                result["match_info"]["home_score"] = (
                    GeniusSportsParser._safe_int(
                        home_score_elem.get_text(strip=True) if home_score_elem else None
                    )
                )

            if away_wrapper and isinstance(away_wrapper, Tag):
                away_name_elem = away_wrapper.find("span", class_="name")
                away_score_elem = away_wrapper.find("div", class_="score")
                result["match_info"]["away_team"] = (
                    away_name_elem.get_text(strip=True) if away_name_elem else None
                )
                result["match_info"]["away_score"] = (
                    GeniusSportsParser._safe_int(
                        away_score_elem.get_text(strip=True) if away_score_elem else None
                    )
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

        # Extract play-by-play events
        playbyplay_div = soup.find("div", id="playbyplay")
        if playbyplay_div and isinstance(playbyplay_div, Tag):
            # Find all event divs
            event_divs = playbyplay_div.find_all("div", class_="pbpa")

            for event_div in event_divs:
                if not isinstance(event_div, Tag):
                    continue

                # Extract event type from classes
                event_classes = event_div.get("class", [])
                event_type = None
                period = None
                team = None

                for cls in event_classes:
                    if cls.startswith("pbpty"):
                        event_type = cls.replace("pbpty", "")
                    elif cls.startswith("per_"):
                        period = cls.replace("per_", "")
                    elif cls.startswith("pbpt"):
                        team_match = re.match(r"pbpt(\d+)", cls)
                        if team_match:
                            team = team_match.group(1)

                # Extract event details
                pbp_team = event_div.find("div", class_="pbp-team")
                if not pbp_team or not isinstance(pbp_team, Tag):
                    continue

                # Get time
                time_elem = pbp_team.find("div", class_="pbp-time")
                time_str = None
                score = None
                period_str = None

                if time_elem and isinstance(time_elem, Tag):
                    period_span = time_elem.find("span", class_="pbp-period")
                    if period_span:
                        period_str = period_span.get_text(strip=True)

                    score_span = time_elem.find("span", class_="pbpsc")
                    if score_span:
                        score = score_span.get_text(strip=True)

                    # Get the time text (excluding period and score spans)
                    time_text_parts = []
                    for content in time_elem.contents:
                        if isinstance(content, NavigableString):
                            text = str(content).strip()
                            if text:
                                time_text_parts.append(text)
                    time_str = " ".join(time_text_parts)

                # Get action description
                action_elem = pbp_team.find("div", class_="pbp-action")
                action = (
                    action_elem.get_text(strip=True)
                    if action_elem and isinstance(action_elem, Tag)
                    else None
                )

                # Extract player name and number from action if present
                player_name = None
                player_number = None
                if action:
                    # Look for player in strong tag first
                    strong = (
                        action_elem.find("strong")
                        if action_elem and isinstance(action_elem, Tag)
                        else None
                    )
                    if strong:
                        player_text = strong.get_text(strip=True)
                        # Format is usually "number, name"
                        if "," in player_text:
                            parts = player_text.split(",", 1)
                            player_number = parts[0].strip()
                            player_name = parts[1].strip()
                        else:
                            player_name = player_text.strip()

                    # Fallback: parse from action text
                    if not player_name and "," in action:
                        number_match = re.match(
                            r"^\s*(\d+)\s*,\s*([^,]+)\s*,", action
                        )
                        if number_match:
                            player_number = number_match.group(1).strip()
                            player_name = number_match.group(2).strip()
                        else:
                            name_match = re.match(r"^\s*([^,]+?)\s*,", action)
                            if name_match:
                                player_name = name_match.group(1).strip()

                # Assign synthetic per-game player id
                player_id = None
                if player_name or player_number:
                    key = (team or "", player_number or "", player_name or "")
                    player_id = player_index.get(key)
                    if player_id is None:
                        player_id = len(result["players"]) + 1
                        player_index[key] = player_id
                        result["players"].append(
                            {
                                "player_id": player_id,
                                "name": player_name,
                                "number": player_number,
                                "team": team,
                            }
                        )

                clean_action = GeniusSportsParser._clean_action_text(
                    action, player_name, player_number, event_type
                )

                # Create event object
                event = {
                    "event_type": event_type,
                    "period": period_str or period,
                    "team": team,
                    "time": time_str,
                    "score": score,
                    "action": clean_action,
                    "player_number": player_number,
                    "player_name": player_name,
                    "player_id": player_id,
                }

                result["events"].append(event)

        # Populate running score for all events
        GeniusSportsParser._populate_running_score(result["events"])

        # Calculate possessions from events
        result["possessions"] = GeniusSportsParser._calculate_possessions(
            result["events"]
        )

        return result

    @staticmethod
    def _clean_action_text(
        action: Optional[str],
        player_name: Optional[str],
        player_number: Optional[str],
        event_type: Optional[str],
    ) -> Optional[str]:
        if not action:
            return None

        text = " ".join(action.split())

        if player_name:
            name_pattern = re.escape(player_name)
            text = re.sub(
                rf"^\s*(\d+\s*,\s*)?{name_pattern}\s*,\s*",
                "",
                text,
            )
        elif player_number:
            num_pattern = re.escape(str(player_number))
            text = re.sub(rf"^\s*{num_pattern}\s*,\s*", "", text)

        normalized = text.lower()
        detail = text

        if event_type == "rebound":
            if "defensive" in normalized:
                detail = "def_rebound"
            elif "offensive" in normalized:
                detail = "off_rebound"
            else:
                detail = "rebound"
        elif event_type in {"2pt", "3pt", "freethrow"}:
            if "made" in normalized:
                detail = "shot_made"
            elif "miss" in normalized:
                detail = "shot_missed"
            else:
                detail = "shot_taken"
        elif event_type == "turnover":
            detail = "turnover"
        elif event_type == "steal":
            detail = "steal"
        elif event_type == "assist":
            detail = "assist"
        elif event_type == "block":
            detail = "block"
        elif event_type == "foul":
            detail = "foul"
        elif event_type == "foulon":
            detail = "fouled"
        elif event_type == "substitution":
            detail = "substitution"
        elif event_type == "timeout":
            detail = "timeout"
        elif event_type == "jumpball":
            detail = "jump_ball"
        elif event_type == "period":
            detail = "period"
        elif event_type == "game":
            detail = "game"

        display_name = player_name or (f"#{player_number}" if player_number else None)
        if display_name:
            return f"{display_name}: {detail}"

        return detail

    @staticmethod
    def _populate_running_score(events: List[Dict[str, Any]]) -> None:
        """
        Populate running score for all events based on scoring events.

        This ensures every event has the current score, not just scoring plays.

        Args:
            events: List of events to populate scores for (modified in place)
        """
        current_score = "0-0"

        for event in events:
            # If this event has a score, update our current score
            if event.get("score"):
                current_score = event["score"]
            # Otherwise, use the current running score
            else:
                event["score"] = current_score

    @staticmethod
    def _calculate_possessions(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate possessions from play-by-play events.

        A possession is when one team has control of the ball. It ends when:
        - Defensive rebound (other team gets possession)
        - Turnover/Steal (other team gets possession)
        - Made basket (other team gets possession)

        Offensive rebounds do not start a new possession - they extend the current possession.

        Args:
            events: List of play-by-play events

        Returns:
            Dictionary with possession statistics and detailed possession list
        """
        possessions: List[Dict[str, Any]] = []
        current_possession: Dict[str, Any] | None = None

        for event in events:
            event_type = event.get("event_type", "")
            team = event.get("team", "")
            time_str = event.get("time", "")
            period = event.get("period", "")
            action = event.get("action", "")
            player_id = event.get("player_id")
            score = event.get("score")

            # Skip neutral events
            if team in ["0", None, ""]:
                continue

            # Parse time to seconds for duration calculation
            # Basketball clocks count DOWN from 10:00 to 0:00
            # Format is MM:SS or MM:SS:SS (subseconds)
            time_seconds = None
            if time_str and ":" in time_str:
                try:
                    parts = time_str.split(":")
                    if len(parts) >= 2:  # MM:SS or MM:SS:subseconds
                        # Always treat as MM:SS (minutes:seconds)
                        time_seconds = int(parts[0]) * 60 + int(parts[1])
                except (ValueError, IndexError):
                    pass

            # Determine if possession changes
            possession_change = False
            new_team = None

            # Made basket - other team gets possession
            if event_type in ["2pt", "3pt", "freethrow"] and "made" in action.lower():
                possession_change = True
                new_team = "1" if team == "2" else "2"

            # Defensive rebound only - other team gets possession
            # Offensive rebounds continue the same possession!
            elif event_type == "rebound" and "defensive" in action.lower():
                possession_change = True
                new_team = team

            # Turnover - other team gets possession
            elif event_type == "turnover":
                possession_change = True
                new_team = "1" if team == "2" else "2"

            # Steal - stealing team gets possession
            elif event_type == "steal":
                possession_change = True
                new_team = team

            # Handle possession change
            if possession_change and new_team and new_team != "0":
                # End current possession
                if current_possession:
                    current_possession["end_time"] = time_str
                    current_possession["end_time_seconds"] = time_seconds
                    current_possession["end_period"] = period
                    current_possession["ending_event"] = {
                        "event_type": event_type,
                        "action": action,
                        "player_id": player_id,
                        "score": score,
                    }

                    # Calculate duration (clock counts down, so start time > end time)
                    if (
                        current_possession["start_time_seconds"] is not None
                        and time_seconds is not None
                        and current_possession["start_period"] == period
                    ):
                        # For same period: start - end (clock counts down)
                        duration = (
                            current_possession["start_time_seconds"] - time_seconds
                        )
                        current_possession["duration_seconds"] = max(0, duration)
                    elif (
                        current_possession["start_time_seconds"] is not None
                        and time_seconds is not None
                    ):
                        # Different periods - can't accurately calculate
                        current_possession["duration_seconds"] = None

                    possessions.append(current_possession)

                # Start new possession
                current_possession = {
                    "possession_number": len(possessions) + 1,
                    "team": new_team,
                    "start_time": time_str,
                    "start_time_seconds": time_seconds,
                    "start_period": period,
                    "starting_event": {
                        "event_type": event_type,
                        "action": action,
                        "player_id": player_id,
                        "score": score,
                    },
                    "end_time": None,
                    "end_time_seconds": None,
                    "end_period": None,
                    "ending_event": None,
                    "duration_seconds": None,
                    "events": [],
                }

            # Add ALL events to current possession (including offensive rebounds)
            if current_possession:
                current_possession["events"].append(
                    {
                        "event_type": event_type,
                        "time": time_str,
                        "action": action,
                        "team": team,
                        "player_id": player_id,
                        "score": score,
                    }
                )

        # Close final possession
        if current_possession:
            possessions.append(current_possession)

        # Calculate statistics
        team1_possessions = [p for p in possessions if p["team"] == "1"]
        team2_possessions = [p for p in possessions if p["team"] == "2"]

        # Calculate average possession length (only for possessions with duration)
        team1_durations = [
            p["duration_seconds"]
            for p in team1_possessions
            if p.get("duration_seconds") is not None
        ]
        team2_durations = [
            p["duration_seconds"]
            for p in team2_possessions
            if p.get("duration_seconds") is not None
        ]

        team1_avg_duration = (
            sum(team1_durations) / len(team1_durations) if team1_durations else 0
        )
        team2_avg_duration = (
            sum(team2_durations) / len(team2_durations) if team2_durations else 0
        )

        return {
            "total_possessions": len(possessions),
            "team1_possessions": len(team1_possessions),
            "team2_possessions": len(team2_possessions),
            "team1_avg_possession_length_seconds": round(team1_avg_duration, 2),
            "team2_avg_possession_length_seconds": round(team2_avg_duration, 2),
            "possessions_list": possessions,
        }

    @staticmethod
    def parse_player_gamelog(
        html_content: str, teams_dict: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Parse player gamelog HTML content.

        Args:
            html_content: HTML content from the gamelog page
            teams_dict: Dictionary mapping team IDs to team names

        Returns:
            Dictionary containing player's team and game statistics
        """
        soup = BeautifulSoup(html_content, "html.parser")

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

    @staticmethod
    def parse_teams_page(html_content: str) -> List[Dict[str, Any]]:
        """
        Parse teams page HTML and extract team data.

        Args:
            html_content: HTML content from the teams page

        Returns:
            List of dictionaries containing team data (id, name)
        """
        soup = BeautifulSoup(html_content, "html.parser")
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

    @staticmethod
    def parse_players_page(html_content: str) -> List[Dict[str, Any]]:
        """
        Parse players page HTML and extract player links.

        Args:
            html_content: HTML content from the players page

        Returns:
            List of dictionaries containing player data (id, name, href)
        """
        soup = BeautifulSoup(html_content, "html.parser")

        # Find all player links
        player_links_html = soup.find_all("a", class_="playername")

        player_links = []
        for link in player_links_html:
            href = link.get("href", "")
            player_name = link.get_text(strip=True)

            # Extract player ID from href
            # Format: /FBAA/en/competition/42145/person/457315?
            match = re.search(r"/person/(\d+)", href)
            if match:
                player_id = match.group(1)
                if player_name:  # Only add if there's a name
                    player_links.append(
                        {"id": player_id, "name": player_name, "href": href}
                    )

        return player_links

    @staticmethod
    def parse_team_roster_page(html_content: str) -> List[Dict[str, Any]]:
        """
        Parse team roster page HTML and extract player links.

        Args:
            html_content: HTML content from the team roster page

        Returns:
            List of dictionaries containing player data (id, name, href)
        """
        soup = BeautifulSoup(html_content, "html.parser")

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

        return unique_players

    @staticmethod
    def parse_team_statistics_page(html_content: str) -> Dict[str, Any]:
        """
        Parse team statistics page HTML and extract player statistics.

        Args:
            html_content: HTML content from the team statistics page

        Returns:
            Dictionary containing team info and three statistical categories
        """
        soup = BeautifulSoup(html_content, "html.parser")

        result: Dict[str, Any] = {
            "team_name": None,
            "team_location": None,
            "averages": [],
            "shooting": [],
            "totals": [],
        }

        # Extract team name
        team_title = soup.find("h1", class_="team-title")
        if team_title and isinstance(team_title, Tag):
            result["team_name"] = team_title.get_text(strip=True)

        # Extract team location from contact details
        contact_div = soup.find("h2", string="Contact Details")
        if contact_div and isinstance(contact_div, Tag):
            parent = contact_div.find_parent()
            if parent and isinstance(parent, Tag):
                # Get text after the h2, before any other tags
                text_parts = []
                for sibling in contact_div.next_siblings:
                    if isinstance(sibling, NavigableString):
                        text = str(sibling).strip()
                        if text:
                            text_parts.append(text)
                    elif isinstance(sibling, Tag) and sibling.name == "br":
                        continue
                    else:
                        break
                if text_parts:
                    result["team_location"] = text_parts[0]

        # Find all statistical tables
        tables = soup.find_all("table", class_="tableClass")

        for table in tables:
            # Find the preceding h4 to determine table type
            h4 = table.find_previous("h4")
            if not h4 or not isinstance(h4, Tag):
                continue

            table_type = h4.get_text(strip=True)

            # Get column headers
            headers = []
            thead = table.find("thead")
            if thead and isinstance(thead, Tag):
                header_row = thead.find("tr")
                if header_row and isinstance(header_row, Tag):
                    for th in header_row.find_all("th"):
                        # Use title attribute if available, otherwise text
                        header = th.get("title", th.get_text(strip=True))
                        headers.append(header)

            # Get player stats
            tbody = table.find("tbody")
            if not tbody or not isinstance(tbody, Tag):
                continue

            players_stats = []
            for row in tbody.find_all("tr"):
                if not isinstance(row, Tag):
                    continue

                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                player_stat: Dict[str, Any] = {}

                for i, cell in enumerate(cells):
                    if i >= len(headers):
                        break

                    header = headers[i]

                    # Handle Player column - extract name and ID
                    if header == "Player":
                        link = cell.find("a")
                        if link and isinstance(link, Tag):
                            player_name = link.get_text(strip=True)
                            href = link.get("href", "")
                            # Extract player ID from href
                            match = re.search(r"/person/(\d+)", str(href))
                            if match:
                                player_stat["player_id"] = match.group(1)
                            player_stat["player_name"] = player_name
                        continue

                    # Get value from cell
                    # First try data-sort-value attribute
                    value = cell.get("data-sort-value")
                    if value is not None:
                        # Try to convert to appropriate type
                        try:
                            if "." in str(value):
                                value = float(value)
                            else:
                                value = int(value)
                        except (ValueError, TypeError):
                            pass  # Keep as string
                    else:
                        # Get text content
                        value = cell.get_text(strip=True)
                        # Try to convert to number for numeric fields
                        if header not in ["Player"]:
                            try:
                                # Check if it's a time format (MM:SS)
                                if ":" in str(value):
                                    value = value  # Keep as string for time
                                elif "." in str(value):
                                    value = float(value)
                                else:
                                    value = int(value)
                            except (ValueError, TypeError):
                                pass  # Keep as string

                    player_stat[header] = value

                if player_stat:
                    players_stats.append(player_stat)

            # Add to appropriate category
            if table_type == "Averages":
                result["averages"] = players_stats
            elif table_type == "Shooting Statistics":
                result["shooting"] = players_stats
            elif table_type == "Totals":
                result["totals"] = players_stats

        return result
