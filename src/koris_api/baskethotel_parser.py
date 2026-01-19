"""Parser for BasketHotel HTML responses."""

from typing import Dict, Any
import re
from bs4 import BeautifulSoup, Tag


class BasketHotelParser:
    """Parser for BasketHotel HTML content."""

    @staticmethod
    def extract_html_from_response(js_response: str) -> str:
        """
        Extract HTML from JavaScript response.

        Args:
            js_response: JavaScript response containing HTML

        Returns:
            Extracted HTML content
        """
        # The response is JavaScript like: MBT.API.update('view4', '<html>...</html>')
        # We need to extract all update calls
        html_parts = []

        idx = 0
        token = "MBT.API.update("
        while True:
            start = js_response.find(token, idx)
            if start == -1:
                break
            first_quote = js_response.find("'", start)
            if first_quote == -1:
                break
            second_quote = js_response.find("'", first_quote + 1)
            if second_quote == -1:
                break
            comma = js_response.find(",", second_quote)
            if comma == -1:
                break
            html_start = js_response.find("'", comma)
            if html_start == -1:
                break

            i = html_start + 1
            escaped = False
            html_end = None
            while i < len(js_response):
                ch = js_response[i]
                if escaped:
                    escaped = False
                else:
                    if ch == "\\":
                        escaped = True
                    elif ch == "'":
                        html_end = i
                        break
                i += 1

            if html_end is None:
                break

            html = js_response[html_start + 1 : html_end]
            # Unescape the HTML string
            html = html.replace("\\n", "\n")
            html = html.replace("\\r", "\r")
            html = html.replace("\\t", "\t")
            html = html.replace("\\'", "'")
            html = html.replace('\\"', '"')
            html = html.replace("\\/", "/")
            html_parts.append(html)
            idx = html_end + 1

        return "\n".join(html_parts) if html_parts else js_response

    @staticmethod
    def parse_game_html(html: str) -> Dict[str, Any]:
        """
        Parse HTML to extract structured game data.

        Args:
            html: HTML content from the game page

        Returns:
            Dictionary containing structured game data
        """
        soup = BeautifulSoup(html, "html.parser")

        game_data: Dict[str, Any] = {
            "teams": {"home": {}, "away": {}},
            "score": {},
            "quarter_scores": [],
            "game_info": {},
            "leaders": [],
            "team_stats": {},
            "player_stats": {"home": [], "away": []},
        }

        # Extract team names from header
        header = soup.find("div", class_="mbt-v2-header")
        if header:
            # Find team names in the header text
            text = header.get_text()
            # Pattern: TeamName1 Score1 - Score2 TeamName2
            lines = [
                line.strip()
                for line in text.split("\n")
                if line.strip() and not line.strip().isdigit() and line.strip() != "-"
            ]
            if len(lines) >= 2:
                game_data["teams"]["home"]["name"] = lines[0]
                game_data["teams"]["away"]["name"] = lines[1]

        # Extract scores
        scores = soup.find_all("div", class_="mbt-v2-game-team-score")
        if len(scores) >= 2:
            try:
                game_data["score"]["home"] = int(scores[0].get_text().strip())
                game_data["score"]["away"] = int(scores[1].get_text().strip())
            except ValueError:
                pass

        # Extract quarter scores
        quarter_scores = soup.find_all(
            "span", class_="mbt-v2-game-quarter-scores-score"
        )
        for i, quarter in enumerate(quarter_scores, 1):
            score_text = quarter.get_text().strip()
            if ":" in score_text:
                try:
                    home_score, away_score = score_text.split(":")
                    game_data["quarter_scores"].append(
                        {
                            "quarter": i,
                            "home": int(home_score.strip()),
                            "away": int(away_score.strip()),
                        }
                    )
                except ValueError:
                    pass

        # Extract game info
        # Date
        date_icon = soup.find("i", class_="fa-calendar")
        if date_icon and date_icon.parent:
            date_text = date_icon.parent.get_text().strip()
            date_match = re.search(r"\d{2}\.\d{2}\.\d{4}", date_text)
            if date_match:
                game_data["game_info"]["date"] = date_match.group()

        # Time
        time_icon = soup.find("i", class_="fa-clock-o")
        if time_icon and time_icon.parent:
            time_text = time_icon.parent.get_text().strip()
            time_match = re.search(r"\d{2}:\d{2}", time_text)
            if time_match:
                game_data["game_info"]["time"] = time_match.group()

        # Attendance
        attendance_icon = soup.find("i", class_="fa-users")
        if attendance_icon and attendance_icon.parent:
            attendance_text = attendance_icon.parent.get_text().strip()
            attendance_match = re.search(r"\d+", attendance_text)
            if attendance_match:
                game_data["game_info"]["attendance"] = int(attendance_match.group())

        # Venue
        venue_icon = soup.find("i", class_="fa-globe")
        if venue_icon and venue_icon.parent:
            venue_text = venue_icon.parent.get_text().strip()
            if venue_text:
                game_data["game_info"]["venue"] = venue_text

        # Game ID
        game_id_match = re.search(r"Ottelunumero:.*?(\d+)", html)
        if game_id_match:
            game_data["game_info"]["game_id"] = game_id_match.group(1)

        # Extract team leaders
        leaders_table = soup.find(
            "table", class_="mbt-v2-game-leaders-comparison-table"
        )
        if leaders_table and isinstance(leaders_table, Tag):
            tbody = leaders_table.find("tbody")
            rows = tbody.find_all("tr") if tbody and isinstance(tbody, Tag) else []
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 5:
                    # Structure: [0]=home img, [1]=home name, [2]=stat type & values, [3]=away name, [4]=empty
                    # Find the stat type in the middle cell
                    stat_span = cells[2].find("span", class_="mbt-v2-text-light")
                    if stat_span:
                        stat_type = stat_span.get_text().strip()

                        # Extract home and away player names
                        home_name = (
                            cells[1]
                            .get_text()
                            .strip()
                            .replace("<br/>", " ")
                            .replace("\n", " ")
                            .strip()
                        )
                        away_name = (
                            cells[3]
                            .get_text()
                            .strip()
                            .replace("<br/>", " ")
                            .replace("\n", " ")
                            .strip()
                        )

                        # Extract values from divs in the middle cell
                        value_divs = cells[2].find_all(
                            "div", style=re.compile(r"font-size")
                        )
                        if len(value_divs) >= 2:
                            home_value = value_divs[0].get_text().strip()
                            away_value = value_divs[1].get_text().strip()
                            try:
                                leader_data = {
                                    "category": stat_type,
                                    "home": {
                                        "player": home_name,
                                        "value": int(home_value),
                                    },
                                    "away": {
                                        "player": away_name,
                                        "value": int(away_value),
                                    },
                                }
                                game_data["leaders"].append(leader_data)
                            except ValueError:
                                continue

        # Extract team stats
        stats_table = soup.find("table", class_="mbt-v2-game-scoring-table")
        if stats_table and isinstance(stats_table, Tag):
            tbody = stats_table.find("tbody")
            rows = tbody.find_all("tr") if tbody and isinstance(tbody, Tag) else []
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    stat_name = cells[0].get_text().strip()
                    home_value = cells[1].get_text().strip()

                    if len(cells) > 2:
                        away_value = cells[2].get_text().strip()
                        game_data["team_stats"][stat_name] = {
                            "home": home_value,
                            "away": away_value,
                        }
                    else:
                        game_data["team_stats"][stat_name] = {"value": home_value}

        return game_data

    @staticmethod
    def parse_boxscore_html(html: str) -> Dict[str, Any]:
        """
        Parse BasketHotel boxscore HTML to extract team totals.

        Returns:
            Dictionary containing team totals and player rows.
        """
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table or not isinstance(table, Tag):
            return {"teams": []}

        teams: list[Dict[str, Any]] = []
        current_team: str | None = None
        current_team_players: list[Dict[str, Any]] = []
        current_team_has_totals = False
        headers: list[str] | None = None
        header_indexes: dict[str, int] = {}

        def _header_index(name: str) -> int | None:
            indices = [i for i, h in enumerate(headers or []) if h.upper() == name]
            return indices[-1] if indices else None

        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if not cells:
                continue

            values = [cell.get_text(strip=True) for cell in cells]
            if not any(values):
                continue

            if values[0] and "MIN" not in values and headers is None:
                current_team = values[0]
                continue

            if "MIN" in values:
                if current_team and current_team_players and not current_team_has_totals:
                    teams.append(
                        {
                            "team_name": current_team,
                            "players": current_team_players,
                        }
                    )
                current_team_players = []
                current_team_has_totals = False
                if values[0] != "MIN":
                    current_team = values[0]
                    headers = ["Player"] + values[1:]
                else:
                    headers = ["Player"] + values
                header_indexes = {
                    "LEV": _header_index("LEV"),
                    "S": _header_index("S"),
                    "R": _header_index("R"),
                }
                continue

            non_empty = [val for val in values if val]
            if len(non_empty) == 1:
                candidate = non_empty[0]
                lowered = candidate.lower()
                if lowered in ("joukkue", "yhteensä", "yhteensa"):
                    continue
                if lowered.startswith("valmentaja"):
                    continue
                current_team = candidate
                continue

            if values[0].lower() in ("yhteensä", "yhteensa"):
                if not headers:
                    continue
                if len(values) == len(headers) - 1:
                    values = [""] + values
                totals: Dict[str, Any] = {"team_name": current_team}
                for key, header_name in (
                    ("rebounds", "LEV"),
                    ("assists", "S"),
                    ("steals", "R"),
                ):
                    idx = header_indexes.get(header_name)
                    if idx is None or idx >= len(values):
                        totals[key] = 0
                        continue
                    try:
                        totals[key] = int(values[idx]) if values[idx] else 0
                    except ValueError:
                        totals[key] = 0
                totals_entry = {
                    "team_name": current_team,
                    "players": current_team_players,
                    "totals": totals,
                    "rebounds": totals.get("rebounds", 0),
                    "assists": totals.get("assists", 0),
                    "steals": totals.get("steals", 0),
                }
                teams.append(totals_entry)
                current_team_players = []
                current_team_has_totals = True
                continue

            if values[0].startswith("Valmentaja"):
                continue

            if values[0].lower() == "joukkue":
                continue

            if headers:
                row_values = list(values)
                if len(row_values) < len(headers):
                    row_values += [""] * (len(headers) - len(row_values))
                if len(row_values) > len(headers):
                    row_values = row_values[: len(headers)]
                player = {headers[idx]: row_values[idx] for idx in range(len(headers))}
                player["team_name"] = current_team
                current_team_players.append(player)
                continue

        if current_team and current_team_players and not current_team_has_totals:
            teams.append(
                {
                    "team_name": current_team,
                    "players": current_team_players,
                }
            )

        return {"teams": teams}
