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

    @staticmethod
    def parse_playbyplay_html(html: str) -> Dict[str, Any]:
        """
        Parse BasketHotel play-by-play HTML to extract game events.

        Returns:
            Dictionary containing parsed play-by-play data.
        """
        soup = BeautifulSoup(html, "html.parser")
        result: Dict[str, Any] = {"events": [], "teams": {}}
        current_period: str | None = "P1"
        inferred_elapsed_clock = False
        max_minutes_seen = 0

        header = soup.find("div", class_="mbt-v2-header")
        if header and isinstance(header, Tag):
            text = header.get_text(separator="\n", strip=True)
            lines = [line for line in text.split("\n") if line]
            if len(lines) >= 2:
                result["teams"]["home"] = lines[0]
                result["teams"]["away"] = lines[1]

        table = soup.find("table", class_=re.compile(r"play-by-play", re.I))
        if not table or not isinstance(table, Tag):
            return result

        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody and isinstance(tbody, Tag) else []

        for row in rows:
            if not isinstance(row, Tag):
                continue

            cells = row.find_all("td")
            if not cells:
                continue

            if (
                len(cells) == 1
                and cells[0].get("colspan")
                and "game-action" in (cells[0].get("class") or [])
            ):
                action_text = cells[0].get_text(" ", strip=True)
                event_type = "game_action"
                period = None
                if "jakso" in action_text.lower():
                    period_match = re.search(r"Jakso\s+(\d+)", action_text, re.I)
                    if period_match:
                        period = period_match.group(1)
                        event_type = "period_end"
                        current_period = f"P{period}"
                if "ottelu päättyi" in action_text.lower():
                    event_type = "game_end"
                result["events"].append(
                    {
                        "event_type": event_type,
                        "period": current_period if event_type == "period_end" else None,
                        "action": action_text,
                        "time": None,
                        "score": None,
                        "team": None,
                        "players": [],
                    }
                )
                continue

            while len(cells) < 4:
                cells.append(None)

            time_text = cells[0].get_text(strip=True) if cells[0] else None
            if time_text and ":" in time_text:
                try:
                    minute_part = int(time_text.split(":", 1)[0])
                    max_minutes_seen = max(max_minutes_seen, minute_part)
                    if minute_part >= 12:
                        inferred_elapsed_clock = True
                except ValueError:
                    pass
            score_text = cells[1].get_text(strip=True) if cells[1] else None
            if score_text and ":" in score_text:
                score_text = score_text.replace(":", "-")

            team_name = None
            if cells[2]:
                team_span = cells[2].find("span", class_="mbt-v2-text-light")
                if team_span:
                    team_name = team_span.get_text(strip=True)
                if not team_name:
                    img = cells[2].find("img")
                    if img and isinstance(img, Tag):
                        team_name = img.get("alt") or None
                if not team_name:
                    team_name = cells[2].get_text(strip=True) or None

            action_cell = cells[3]
            if not action_cell or not isinstance(action_cell, Tag):
                continue

            raw_action = action_cell.get_text(" ", strip=True)
            players: list[Dict[str, Any]] = []
            seen: set[tuple] = set()

            for link in action_cell.find_all("a"):
                if not isinstance(link, Tag):
                    continue
                player_name = link.get_text(strip=True) or None
                if not player_name:
                    continue
                player_id = link.get("player_id") or None
                season_id = link.get("season_id") or None
                href = link.get("href") or ""
                league_id_match = re.search(r"league_id=(\d+)", href)
                league_id = league_id_match.group(1) if league_id_match else None

                number = None
                parent_text = (
                    " ".join(link.parent.stripped_strings)
                    if link.parent and isinstance(link.parent, Tag)
                    else ""
                )
                if parent_text:
                    number_match = re.search(
                        rf"\((\d+)\)\s*{re.escape(player_name)}", parent_text
                    )
                    if number_match:
                        number = number_match.group(1)
                if number is None:
                    number_match = re.search(
                        rf"\((\d+)\)\s*{re.escape(player_name)}", raw_action
                    )
                    if number_match:
                        number = number_match.group(1)

                key = (player_id or "", player_name or "", number or "")
                if key in seen:
                    continue
                seen.add(key)
                players.append(
                    {
                        "player_id": player_id,
                        "season_id": season_id,
                        "league_id": league_id,
                        "name": player_name,
                        "number": number,
                    }
                )

            if not players:
                # Fallback for widget responses without player links.
                for number, name in re.findall(
                    r"\((\d+)\)\s*([A-Za-zÀ-ÖØ-öø-ÿ'’.\\. \\-]+)",
                    raw_action,
                ):
                    cleaned_name = " ".join(name.split()).strip()
                    if cleaned_name:
                        cleaned_name = re.split(
                            r"\b(tuli|meni|onnistui|epäonnistui|otti|antoi|teki|riisti|torjui|hyökkäyslevypallo|puolustuslevypallo)\b",
                            cleaned_name,
                            1,
                            flags=re.IGNORECASE,
                        )[0].strip()
                    if not cleaned_name:
                        continue
                    key = ("", cleaned_name, number)
                    if key in seen:
                        continue
                    seen.add(key)
                    players.append(
                        {
                            "player_id": None,
                            "season_id": None,
                            "league_id": None,
                            "name": cleaned_name,
                            "number": number,
                        }
                    )

            action = raw_action
            for player in players:
                if player.get("name"):
                    name = re.escape(player["name"])
                    action = re.sub(rf"\(\d+\)\s*{name}", "", action)
                    action = re.sub(rf"{name}", "", action)
            action = " ".join(action.split()).strip() or raw_action

            lowered = action.lower()
            event_type = None
            if "tuli" in lowered and "kentälle" in lowered:
                event_type = "sub_in"
            elif "meni" in lowered and "vaihtoon" in lowered:
                event_type = "sub_out"
            elif "onnistui" in lowered and "pisteen" in lowered:
                event_type = "shot_made"
            elif "epäonnistui" in lowered and "pisteen" in lowered:
                event_type = "shot_missed"
            elif "syötön" in lowered:
                event_type = "assist"
            elif "puolustuslevypallon" in lowered:
                event_type = "def_rebound"
            elif "hyökkäyslevypallon" in lowered:
                event_type = "off_rebound"

            period_value = current_period
            if inferred_elapsed_clock and time_text and ":" in time_text:
                try:
                    minutes = int(time_text.split(":", 1)[0])
                    period_value = f"P{minutes // 10 + 1}"
                except ValueError:
                    period_value = current_period

            result["events"].append(
                {
                    "event_type": event_type,
                    "period": period_value,
                    "time": time_text or None,
                    "score": score_text or None,
                    "team": team_name,
                    "action": action,
                    "action_raw": raw_action,
                    "players": players,
                }
            )

        return result

    @staticmethod
    def normalize_playbyplay_to_genius_format(
        playbyplay: Dict[str, Any],
        team_map: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """
        Normalize BasketHotel play-by-play to match Genius Sports format.

        Returns:
            Dictionary containing match_info, events, players, possessions.
        """
        team_map = team_map or {}
        from .genius_parser import GeniusSportsParser

        players: list[Dict[str, Any]] = []
        player_index: Dict[tuple, int] = {}

        def _team_id(team_name: str | None) -> str | None:
            if not team_name:
                return None
            if team_name in team_map:
                return team_map[team_name]
            return None

        def _register_player(
            team_id: str | None, number: str | None, name: str | None
        ) -> int | None:
            if not name and not number:
                return None
            key = (team_id or "", number or "", name or "")
            existing = player_index.get(key)
            if existing is not None:
                return existing
            player_id = len(players) + 1
            player_index[key] = player_id
            players.append(
                {
                    "player_id": player_id,
                    "name": name,
                    "number": number,
                    "team": team_id,
                }
            )
            return player_id

        def _infer_event_type(action_raw: str, base_type: str | None) -> str | None:
            lowered = action_raw.lower()
            if base_type in {"sub_in", "sub_out"}:
                return "substitution"
            if "hyökkääjän virhe" in lowered:
                return "turnover"
            if "huonon syötön" in lowered:
                return "turnover"
            if "vapaaheitto" in lowered:
                return "freethrow"
            if "3 pisteen" in lowered:
                return "3pt"
            if "2 pisteen" in lowered:
                return "2pt"
            if base_type in {"shot_made", "shot_missed"}:
                return "shot"
            if base_type in {"def_rebound", "off_rebound"}:
                return "rebound"
            if base_type == "assist":
                return "assist"
            if "riisti" in lowered:
                return "steal"
            if "torjui" in lowered:
                return "block"
            if "virhe" in lowered:
                return "foul"
            if "syötön" in lowered:
                return "assist"
            if "levypallon" in lowered:
                return "rebound"
            return base_type

        def _format_action(player_name: str | None, detail: str | None) -> str | None:
            if not detail:
                return None
            if player_name:
                return f"{player_name}: {detail}"
            return detail

        events: list[Dict[str, Any]] = []
        current_score = "0-0"
        for event in playbyplay.get("events", []):
            if not isinstance(event, dict):
                continue
            base_type = event.get("event_type")
            action_raw = event.get("action_raw") or event.get("action") or ""
            event_type = _infer_event_type(action_raw, base_type)
            team_name = event.get("team")
            team_id = _team_id(team_name)
            time_str = event.get("time")
            score = event.get("score")
            if score:
                current_score = score
            else:
                score = current_score

            players_in_event = event.get("players") or []
            primary_player = players_in_event[0] if players_in_event else None
            player_name = primary_player.get("name") if primary_player else None
            player_number = primary_player.get("number") if primary_player else None
            player_id = _register_player(team_id, player_number, player_name)

            detail = None
            lowered = action_raw.lower()
            if event_type in {"2pt", "3pt", "freethrow"}:
                if "onnistui" in lowered:
                    detail = "shot_made"
                elif "epäonnistui" in lowered:
                    detail = "shot_missed"
                else:
                    detail = "shot_taken"
            elif event_type == "rebound":
                if "puolustus" in lowered:
                    detail = "def_rebound"
                elif "hyökkäys" in lowered:
                    detail = "off_rebound"
                else:
                    detail = "rebound"
            elif event_type == "turnover":
                detail = "turnover"
            elif event_type == "steal":
                detail = "steal"
            elif event_type == "block":
                detail = "block"
            elif event_type == "assist":
                detail = "assist"
            elif event_type == "foul":
                detail = "foul"
            elif event_type == "substitution":
                detail = "substitution"

            events.append(
                {
                    "event_type": event_type,
                    "period": event.get("period"),
                    "team": team_id,
                    "time": time_str,
                    "score": score,
                    "action": _format_action(player_name, detail) or action_raw,
                    "player_number": player_number,
                    "player_name": player_name,
                    "player_id": player_id,
                }
            )

        match_info = {
            "home_team": playbyplay.get("game_teams", {})
            .get("home", {})
            .get("name"),
            "away_team": playbyplay.get("game_teams", {})
            .get("away", {})
            .get("name"),
            "home_score": playbyplay.get("score", {}).get("home"),
            "away_score": playbyplay.get("score", {}).get("away"),
            "status": None,
            "datetime": playbyplay.get("game_info", {}).get("date"),
            "venue": playbyplay.get("game_info", {}).get("venue"),
        }

        if not events or events[0].get("event_type") != "game":
            events.insert(
                0,
                {
                    "event_type": "game",
                    "period": "reg",
                    "team": "0",
                    "time": "",
                    "score": "0-0",
                    "action": "game",
                    "player_number": None,
                    "player_name": None,
                    "player_id": None,
                },
            )
            events.insert(
                1,
                {
                    "event_type": "period",
                    "period": "reg",
                    "team": "0",
                    "time": "",
                    "score": "0-0",
                    "action": "period",
                    "player_number": None,
                    "player_name": None,
                    "player_id": None,
                },
            )

        GeniusSportsParser._populate_running_score(events)
        possessions = GeniusSportsParser._calculate_possessions(events)

        return {
            "match_info": match_info,
            "events": events,
            "players": players,
            "possessions": possessions,
        }
