import json
from pathlib import Path
from typing import Optional
import pandas as pd
import requests
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Button, Static, Select, DataTable
from textual.containers import Container, Horizontal, VerticalScroll
from textual.binding import Binding
from textual.screen import Screen
from .api import KorisAPI


class MatchViewScreen(Screen):
    """Screen to display detailed match information."""

    CSS = """
    MatchViewScreen {
        background: $surface;
    }
    
    #match_content {
        height: 1fr;
        padding: 2;
        overflow-y: auto;
    }
    
    .match_header {
        text-align: center;
        color: $primary;
        text-style: bold;
        margin: 1 0;
    }
    
    .match_section {
        margin: 1 0;
        padding: 1;
        background: $panel;
        border: solid $primary;
    }
    
    DataTable {
        height: auto;
        margin: 1 0;
    }
    
    Button {
        margin: 1;
        width: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, match_id: str, home_team: str, away_team: str):
        super().__init__()
        self.match_id = match_id
        self.home_team = home_team
        self.away_team = away_team
        self.match_data = None

    def compose(self) -> ComposeResult:
        """Create the match view layout."""
        yield Header()
        with VerticalScroll(id="match_content"):
            yield Static(
                f"Loading {self.home_team} vs {self.away_team}...",
                id="match_info_display",
            )
            yield Static("", id="home_team_header")
            yield DataTable(id="home_players_table")
            yield Static("", id="away_team_header")
            yield DataTable(id="away_players_table")
            with Horizontal():
                yield Button("Back", id="btn_back", variant="primary")
                yield Button(
                    "Back to Matches", id="btn_back_to_matches", variant="default"
                )
        yield Footer()

    def on_mount(self) -> None:
        """Fetch and display match data when screen is mounted."""
        self.load_match_data()

    def load_match_data(self) -> None:
        """Fetch and display match information."""
        display = self.query_one("#match_info_display", Static)

        try:
            display.update("Loading match data...")
            data = KorisAPI.get_match(self.match_id)

            if "match" in data:
                self.match_data = data["match"]
                self.render_match_info()
            else:
                display.update(f"No data found for match {self.match_id}")

        except Exception as e:
            display.update(f"Error loading match data: {str(e)}")

    def render_match_info(self) -> None:
        """Render the match information."""
        if not self.match_data:
            return

        match = self.match_data

        # Build match info display
        info_lines = []

        # Header
        info_lines.append(f"[bold cyan]{'=' * 80}[/bold cyan]")
        info_lines.append(
            f"[bold yellow]{match.get('club_A_name', 'N/A')} vs {match.get('club_B_name', 'N/A')}[/bold yellow]"
        )
        info_lines.append(f"[bold cyan]{'=' * 80}[/bold cyan]\n")

        # Match Details
        info_lines.append("[bold green]MATCH INFORMATION[/bold green]")
        info_lines.append(
            f"Date: {match.get('date', 'N/A')} at {match.get('time', 'N/A')}"
        )
        info_lines.append(
            f"Venue: {match.get('venue_name', 'N/A')}, {match.get('venue_city', 'N/A')}"
        )
        info_lines.append(f"Competition: {match.get('competition_name', 'N/A')}")
        info_lines.append(f"Category: {match.get('category_name', 'N/A')}")
        info_lines.append(f"Status: {match.get('status', 'N/A')}")

        # Score
        if match.get("fs_A") and match.get("fs_B"):
            info_lines.append(
                f"\n[bold]Final Score: {match.get('fs_A')} - {match.get('fs_B')}[/bold]"
            )

            # Quarter scores if available
            quarters = []
            for i in range(1, 5):
                q_a = match.get(f"q{i}_A")
                q_b = match.get(f"q{i}_B")
                if q_a and q_b:
                    quarters.append(f"Q{i}: {q_a}-{q_b}")

            if quarters:
                info_lines.append(f"Quarters: {' | '.join(quarters)}")

        # Referees
        if "referees" in match and match["referees"]:
            info_lines.append("\n[bold green]REFEREES[/bold green]")
            for ref in match["referees"]:
                name = f"{ref.get('first_name', '')} {ref.get('last_name', '')}".strip()
                role = ref.get("referee_role", "Referee")
                info_lines.append(f"  {role}: {name}")

        # Update the display
        display = self.query_one("#match_info_display", Static)
        display.update("\n".join(info_lines))

        # Render player stats tables
        self.render_player_stats()

    def render_player_stats(self) -> None:
        """Render player statistics tables."""
        if not self.match_data:
            return

        match = self.match_data

        # Get lineups (player stats)
        lineups = match.get("lineups", [])

        # Separate by team
        team_a_players = [
            p for p in lineups if p.get("team_id") == match.get("team_A_id")
        ]
        team_b_players = [
            p for p in lineups if p.get("team_id") == match.get("team_B_id")
        ]

        # Home team players (Team A)
        home_table = self.query_one("#home_players_table", DataTable)
        home_table.clear(columns=True)

        # Add columns with fixed widths for consistency
        home_table.add_column("#", width=5)
        home_table.add_column("Player", width=25)
        home_table.add_column("PTS", width=6)
        home_table.add_column("FG", width=6)
        home_table.add_column("3PT", width=6)
        home_table.add_column("FT", width=6)
        home_table.add_column("AST", width=6)
        home_table.add_column("BLK", width=6)
        home_table.add_column("FOUL", width=6)
        home_table.add_column("MIN", width=6)

        if team_a_players:
            home_table.show_header = True
            home_table.zebra_stripes = True
            home_table.cursor_type = "none"

            # Add title
            home_header = self.query_one("#home_team_header", Static)
            home_header.update(
                f"\n[bold cyan]{match.get('club_A_name', 'Home Team')} - Player Statistics[/bold cyan]"
            )

            for player in sorted(
                team_a_players, key=lambda p: int(p.get("pos_id", "999"))
            ):
                home_table.add_row(
                    player.get("shirt_number", "-"),
                    player.get("player_name", "Unknown"),
                    str(player.get("points", "0")),
                    str(player.get("goals", "0")),  # Field goals made
                    str(player.get("goals", "0")),  # 3-pointers (same as goals for now)
                    str(
                        player.get("goals", "0")
                    ),  # Free throws (same as goals for now)
                    str(player.get("assists", "0")),
                    str(player.get("blocks", "0")),
                    str(player.get("fouls", "0")),
                    str(player.get("playing_time_min", "0")),
                )

        # Away team players (Team B)
        away_table = self.query_one("#away_players_table", DataTable)
        away_table.clear(columns=True)

        # Add columns with same fixed widths for consistency
        away_table.add_column("#", width=5)
        away_table.add_column("Player", width=25)
        away_table.add_column("PTS", width=6)
        away_table.add_column("FG", width=6)
        away_table.add_column("3PT", width=6)
        away_table.add_column("FT", width=6)
        away_table.add_column("AST", width=6)
        away_table.add_column("BLK", width=6)
        away_table.add_column("FOUL", width=6)
        away_table.add_column("MIN", width=6)

        if team_b_players:
            away_table.show_header = True
            away_table.zebra_stripes = True
            away_table.cursor_type = "none"

            # Add title for away team
            away_header = self.query_one("#away_team_header", Static)
            away_header.update(
                f"\n[bold cyan]{match.get('club_B_name', 'Away Team')} - Player Statistics[/bold cyan]"
            )

            for player in sorted(
                team_b_players, key=lambda p: int(p.get("pos_id", "999"))
            ):
                away_table.add_row(
                    player.get("shirt_number", "-"),
                    player.get("player_name", "Unknown"),
                    str(player.get("points", "0")),
                    str(player.get("goals", "0")),
                    str(player.get("goals", "0")),
                    str(player.get("goals", "0")),
                    str(player.get("assists", "0")),
                    str(player.get("blocks", "0")),
                    str(player.get("fouls", "0")),
                    str(player.get("playing_time_min", "0")),
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_back_to_matches":
            # Pop all screens until we're back at the main screen
            while len(self.app.screen_stack) > 1:
                self.app.pop_screen()

    def action_back(self) -> None:
        """Go back to the main screen."""
        self.app.pop_screen()


class TeamViewScreen(Screen):
    """Screen to display detailed team information."""

    CSS = """
    TeamViewScreen {
        background: $surface;
    }
    
    #team_content {
        height: 1fr;
        padding: 2;
        overflow-y: auto;
    }
    
    .team_header {
        text-align: center;
        color: $primary;
        text-style: bold;
        margin: 1 0;
    }
    
    .team_section {
        margin: 1 0;
        padding: 1;
        background: $panel;
        border: solid $primary;
    }
    
    DataTable {
        height: auto;
        margin: 1 0;
    }
    
    Button {
        margin: 1;
        width: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, team_id: str, team_name: str, season: Optional[str] = None):
        super().__init__()
        self.team_id = team_id
        self.team_name = team_name
        self.season = season
        self.team_data = None
        self.team_matches: list = []

    def compose(self) -> ComposeResult:
        """Create the team view layout."""
        yield Header()
        with VerticalScroll(id="team_content"):
            yield Static(f"Loading {self.team_name}...", id="team_info_display")
            yield DataTable(id="players_table")
            yield Static("\n[bold green]TEAM MATCHES[/bold green]", id="matches_header")
            yield DataTable(id="team_matches_table")
            with Horizontal():
                yield Button("Back", id="btn_back", variant="primary")
                yield Button(
                    "Back to Matches", id="btn_back_to_matches", variant="default"
                )
        yield Footer()

    def on_mount(self) -> None:
        """Fetch and display team data when screen is mounted."""
        self.load_team_data()
        self.load_team_matches()

    def load_team_data(self) -> None:
        """Fetch and display team information."""
        display = self.query_one("#team_info_display", Static)

        try:
            display.update("Loading team data...")
            data = KorisAPI.get_team(self.team_id)

            if "team" in data:
                self.team_data = data["team"]
                self.render_team_info()
            else:
                display.update(f"No data found for {self.team_name}")

        except Exception as e:
            display.update(f"Error loading team data: {str(e)}")

    def render_team_info(self) -> None:
        """Render the team information."""
        if not self.team_data:
            return

        team = self.team_data

        # Build comprehensive team info display
        info_sections = []

        # Header
        info_sections.append(f"[bold cyan]{'=' * 60}[/bold cyan]")
        info_sections.append(
            f"[bold yellow]{team.get('team_name', 'N/A')}[/bold yellow]"
        )
        info_sections.append(f"[bold cyan]{'=' * 60}[/bold cyan]\n")

        # Add season note if viewing a specific season
        if self.season:
            info_sections.append(f"[italic]Viewing season: {self.season}[/italic]")
            info_sections.append(
                "[italic dim]Note: Team roster and info shows current data, matches are filtered by season[/italic dim]\n"
            )

        # Basic Info Section
        info_sections.append("[bold green]TEAM INFORMATION[/bold green]")
        info_sections.append(f"Club: {team.get('club_name', 'N/A')}")
        info_sections.append(f"Abbreviation: {team.get('club_abbrevation', 'N/A')}")
        info_sections.append(f"City: {team.get('club_city_name', 'N/A')}")
        info_sections.append(f"Home Venue: {team.get('home_venue_name', 'N/A')}")
        info_sections.append(f"Gender: {team.get('gender_fi', 'N/A')}")

        if team.get("club_www"):
            info_sections.append(f"Website: {team.get('club_www')}")

        info_sections.append("")

        # Primary Category
        if "primary_category" in team:
            cat = team["primary_category"]
            info_sections.append("[bold green]CURRENT COMPETITION[/bold green]")
            info_sections.append(f"Category: {cat.get('category_name', 'N/A')}")
            info_sections.append(f"Competition: {cat.get('competition_name', 'N/A')}")
            info_sections.append("")

        # Coaching Staff
        if "officials" in team and team["officials"]:
            info_sections.append("[bold green]COACHING STAFF[/bold green]")
            for official in team["officials"]:
                role = official.get("official_role", "Coach")
                name = f"{official.get('first_name', '')} {official.get('last_name', '')}".strip()
                info_sections.append(f"  {role}: {name}")
            info_sections.append("")

        # Update the display
        display = self.query_one("#team_info_display", Static)
        display.update("\n".join(info_sections))

        # Render players in DataTable
        if "players" in team and team["players"]:
            info_sections.append(
                f"\n[bold green]PLAYERS ({team.get('players_count', len(team['players']))})[/bold green]"
            )
            display.update("\n".join(info_sections))

            players_table = self.query_one("#players_table", DataTable)
            players_table.clear(columns=True)
            players_table.add_columns(
                "#", "Name", "Position", "Height", "Birth Year", "Nationality"
            )
            players_table.show_header = True
            players_table.zebra_stripes = True
            players_table.cursor_type = "none"

            for player in sorted(
                team["players"],
                key=lambda p: int(p.get("shirt_number", "999"))
                if p.get("shirt_number", "").isdigit()
                else 999,
            ):
                number = player.get("shirt_number", "-")
                name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                position = player.get("position_fi", "-")
                height = f"{player.get('height')}cm" if player.get("height") else "-"
                birthyear = player.get("birthyear", "-")
                nationality = player.get("nationality", "-")

                players_table.add_row(
                    number, name, position, height, birthyear, nationality
                )

    def load_team_matches(self) -> None:
        """Fetch and display team matches."""
        if not self.team_data:
            matches_header = self.query_one("#matches_header", Static)
            matches_header.update(
                "\n[bold red]TEAM MATCHES[/bold red] - No team data available"
            )
            return

        try:
            # Fetch matches directly by team_id
            matches_data = KorisAPI.get_matches(team_id=str(self.team_id))

            if "matches" not in matches_data:
                matches_header = self.query_one("#matches_header", Static)
                matches_header.update(
                    "\n[bold yellow]TEAM MATCHES[/bold yellow] - No matches found in response"
                )
                return

            if len(matches_data["matches"]) == 0:
                matches_header = self.query_one("#matches_header", Static)
                matches_header.update(
                    "\n[bold yellow]TEAM MATCHES[/bold yellow] - No games found for this team"
                )
                return

            # Process matches
            self.team_matches = []
            for match in matches_data["matches"]:
                # Filter by season if specified
                if self.season and match.get("season_id") != self.season:
                    continue

                # Determine if this team is home or away
                is_home = match.get("team_A_id") == self.team_id

                home_team = match.get("club_A_name", "N/A")
                away_team = match.get("club_B_name", "N/A")
                home_score = match.get("fs_A", "")
                away_score = match.get("fs_B", "")

                # Handle empty or missing scores
                if not home_score or home_score == "":
                    home_score = "-"
                if not away_score or away_score == "":
                    away_score = "-"

                # Determine result
                result = "-"
                if home_score != "-" and away_score != "-":
                    try:
                        home_score_int = int(home_score)
                        away_score_int = int(away_score)

                        if is_home:
                            if home_score_int > away_score_int:
                                result = "W"
                            elif home_score_int < away_score_int:
                                result = "L"
                            else:
                                result = "D"
                        else:
                            if away_score_int > home_score_int:
                                result = "W"
                            elif away_score_int < home_score_int:
                                result = "L"
                            else:
                                result = "D"
                    except ValueError:
                        # If score can't be converted to int, leave result as "-"
                        pass

                self.team_matches.append(
                    {
                        "date": match.get("date", "N/A"),
                        "time": match.get("time", "N/A")[:5]
                        if match.get("time")
                        else "N/A",
                        "opponent": away_team if is_home else home_team,
                        "opponent_id": match.get("team_B_id")
                        if is_home
                        else match.get("team_A_id"),
                        "venue": "Home" if is_home else "Away",
                        "score": f"{home_score} - {away_score}",
                        "result": result,
                        "match_id": match.get("match_id"),
                        "is_played": home_score != "-" and away_score != "-",
                    }
                )

            # Check if any matches were found after filtering
            if len(self.team_matches) == 0:
                matches_header = self.query_one("#matches_header", Static)
                season_msg = f" for season {self.season}" if self.season else ""
                matches_header.update(
                    f"\n[bold yellow]TEAM MATCHES[/bold yellow] - No games found{season_msg}"
                )
                return

            # Sort by date
            self.team_matches.sort(key=lambda m: m["date"])

            # Render the matches
            self.render_team_matches()

        except requests.exceptions.HTTPError as e:
            matches_header = self.query_one("#matches_header", Static)
            status_code = e.response.status_code if e.response else "unknown"
            matches_header.update(
                f"\n[bold red]TEAM MATCHES[/bold red] - HTTP Error {status_code}: {str(e)}"
            )
        except Exception as e:
            matches_header = self.query_one("#matches_header", Static)
            matches_header.update(
                f"\n[bold red]TEAM MATCHES[/bold red] - Error: {str(e)}"
            )

    def render_team_matches(self) -> None:
        """Render team matches in a DataTable."""
        if not self.team_matches:
            matches_header = self.query_one("#matches_header", Static)
            matches_header.update(
                "\n[bold green]TEAM MATCHES[/bold green] - No matches found"
            )
            return

        matches_table = self.query_one("#team_matches_table", DataTable)
        matches_table.clear(columns=True)
        matches_table.add_columns(
            "Date", "Time", "Opponent", "Venue", "Score", "Result"
        )
        matches_table.show_header = True
        matches_table.zebra_stripes = True
        matches_table.cursor_type = "cell"

        for match in self.team_matches:
            matches_table.add_row(
                match["date"],
                match["time"],
                match["opponent"],
                match["venue"],
                match["score"],
                match["result"],
            )

        # Update header with count
        matches_header = self.query_one("#matches_header", Static)
        played_count = sum(1 for m in self.team_matches if m["is_played"])
        total_count = len(self.team_matches)
        season_msg = f" ({self.season})" if self.season else ""
        matches_header.update(
            f"\n[bold green]TEAM MATCHES{season_msg}[/bold green] - {played_count} played, {total_count - played_count} upcoming"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_back_to_matches":
            # Pop all screens until we're back at the main screen
            while len(self.app.screen_stack) > 1:
                self.app.pop_screen()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle cell selection in the team matches table."""
        table = event.data_table

        # Only handle clicks on the team_matches_table
        if table.id != "team_matches_table":
            return

        # Prevent event from propagating
        event.stop()

        row_index = event.coordinate.row
        col_index = event.coordinate.column

        if row_index >= len(self.team_matches):
            return

        match = self.team_matches[row_index]

        # Columns: "Date"(0), "Time"(1), "Opponent"(2), "Venue"(3), "Score"(4), "Result"(5)

        if col_index == 0:  # Date - show match view for played matches
            if match["is_played"]:
                match_id = match.get("match_id")
                if match_id:
                    # Determine home and away team names based on venue
                    if match["venue"] == "Home":
                        home_team = self.team_name
                        away_team = match["opponent"]
                    else:
                        home_team = match["opponent"]
                        away_team = self.team_name

                    self.app.push_screen(
                        MatchViewScreen(
                            str(match_id),
                            home_team,
                            away_team,
                        )
                    )

        elif col_index == 2:  # Opponent - navigate to opponent team view
            opponent_id = match.get("opponent_id")
            opponent_name = match.get("opponent")
            if opponent_id and opponent_name:
                self.app.push_screen(
                    TeamViewScreen(str(opponent_id), opponent_name, self.season)
                )

        elif col_index == 4:  # Score - show match view for played matches
            if match["is_played"]:
                match_id = match.get("match_id")
                if match_id:
                    # Determine home and away team names based on venue
                    if match["venue"] == "Home":
                        home_team = self.team_name
                        away_team = match["opponent"]
                    else:
                        home_team = match["opponent"]
                        away_team = self.team_name

                    self.app.push_screen(
                        MatchViewScreen(
                            str(match_id),
                            home_team,
                            away_team,
                        )
                    )

    def action_back(self) -> None:
        """Go back to the main screen."""
        self.app.pop_screen()


class KorisApp(App):
    """A Textual app for browsing Koripallo API data."""

    CSS = """
    Screen {
        background: $surface;
    }
    
    #status {
        height: 3;
        background: $panel;
        color: $text;
        padding: 1;
    }
    
    #controls {
        height: auto;
        background: $panel;
        padding: 1;
    }
    
    Horizontal {
        height: auto;
    }
    
    Select {
        width: 1fr;
        margin: 0 1;
    }
    
    Button {
        margin: 0 1;
        width: auto;
    }
    
    DataTable {
        height: 1fr;
        margin: 1;
    }
    
    .info {
        color: $success;
    }
    
    .error {
        color: $error;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.categories = self.load_categories()
        self.current_category = "2"
        self.current_competition_id = "huki2526"
        self.current_data = None
        self.seasons = {}  # Will be populated when category data is fetched
        self.current_season = None
        self.save_format = "json"  # Default save format
        self.matches_data = []  # Store matches for saving
        self.show_upcoming = True  # Show upcoming games by default
        self.last_fetch_time = 0  # Store last fetch duration

    def load_categories(self) -> dict:
        """Load categories from JSON file"""
        categories_path = Path(__file__).parent.parent.parent / "categories.json"
        if categories_path.exists():
            with open(categories_path) as f:
                return json.load(f)
        return {
            "2": {"category_name": "Miesten I divisioona A"},
            "4": {"category_name": "Korisliiga"},
        }

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()

        with Container(id="controls"):
            with Horizontal():
                # Create category options for Select widget
                category_options = [
                    (f"{cat_id}: {cat_data['category_name']}", cat_id)
                    for cat_id, cat_data in self.categories.items()
                ]
                yield Select(
                    options=category_options,
                    value=self.current_category,
                    id="category_select",
                    prompt="Select Category",
                )
                yield Select(
                    options=[("Loading...", "")],
                    id="season_select",
                    prompt="Select Season",
                    allow_blank=False,
                )
            yield Static("")  # Empty row for spacing
            with Horizontal():
                yield Select(
                    options=[
                        ("Show All Matches", "all"),
                        ("Played Only", "played"),
                        ("Upcoming Only", "upcoming"),
                    ],
                    value="all",
                    id="filter_select",
                    prompt="Filter",
                )
                yield Select(
                    options=[("JSON", "json"), ("CSV", "csv"), ("Excel", "excel")],
                    value="json",
                    id="format_select",
                    prompt="Save Format",
                )
                yield Button("Save Data", id="btn_save", variant="success")

        yield Static("Ready - Select a category to load seasons", id="status")
        yield DataTable(id="data_table")
        yield Footer()

    def on_mount(self) -> None:
        """Set up the table when the app starts"""
        table = self.query_one(DataTable)
        table.cursor_type = "cell"
        # Auto-load seasons for the default category
        self.load_seasons()
        # Auto-fetch matches for the default season (will be set after seasons load)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle category selection changes"""
        if event.select.id == "category_select":
            # Ignore blank selections
            if event.value == Select.BLANK or not event.value:
                return

            self.current_category = str(event.value)
            status = self.query_one("#status", Static)
            category_name = self.categories[self.current_category]["category_name"]
            status.update(f"Selected: {category_name} - Loading seasons...")
            # Auto-load seasons when category changes
            self.load_seasons()
        elif event.select.id == "season_select":
            # Ignore blank selections
            if event.value == Select.BLANK or not event.value:
                return

            if str(event.value) and str(event.value) in self.seasons:
                season_data = self.seasons[str(event.value)]
                self.current_season = str(event.value)
                self.current_competition_id = season_data["competition_id"]
                status = self.query_one("#status", Static)
                status.update(
                    f"Selected season: {season_data['season_name']} - Loading matches..."
                )
                # Auto-fetch matches when season changes
                self.fetch_matches()
        elif event.select.id == "format_select":
            # Ignore blank selections
            if event.value == Select.BLANK or not event.value:
                return
            self.save_format = str(event.value)
        elif event.select.id == "filter_select":
            # Ignore blank selections
            if event.value == Select.BLANK or not event.value:
                return
            # Re-render matches with new filter
            self.render_matches()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        if event.button.id == "btn_save":
            self.save_data()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle table cell selection to show team info"""
        filter_select = self.query_one("#filter_select", Select)

        # Get current filter to find the right match
        current_filter = (
            str(filter_select.value) if filter_select.value != Select.BLANK else "all"
        )

        # Filter matches same way as render_matches
        filtered_matches = []
        for match in self.matches_data:
            if current_filter == "all":
                filtered_matches.append(match)
            elif current_filter == "played" and match["is_played"]:
                filtered_matches.append(match)
            elif current_filter == "upcoming" and not match["is_played"]:
                filtered_matches.append(match)

        # Get the row and column indices
        row_index = event.coordinate.row
        col_index = event.coordinate.column

        if row_index < len(filtered_matches):
            match = filtered_matches[row_index]

            # Check which cell was clicked (column index)
            # Columns: "Home Team"(0), "Score"(1), "Away Team"(2), "Date"(3), "Time"(4)

            if col_index == 0:  # Home Team
                team_id = match.get("Home Team ID")
                team_name = match.get("Home Team")
                if team_id and team_name:
                    self.push_screen(
                        TeamViewScreen(
                            str(team_id), str(team_name), self.current_season
                        )
                    )

            elif col_index == 1:  # Score - show match view for played matches only
                if match["is_played"]:
                    match_id = match.get("Match ID")
                    if match_id:
                        self.push_screen(
                            MatchViewScreen(
                                str(match_id),
                                match.get("Home Team", ""),
                                match.get("Away Team", ""),
                            )
                        )

            elif col_index == 2:  # Away Team
                team_id = match.get("Away Team ID")
                team_name = match.get("Away Team")
                if team_id and team_name:
                    self.push_screen(
                        TeamViewScreen(
                            str(team_id), str(team_name), self.current_season
                        )
                    )

    def action_refresh(self) -> None:
        """Refresh the current view"""
        if self.current_data:
            self.fetch_matches()

    def load_seasons(self) -> None:
        """Load available seasons for the current category"""
        status = self.query_one("#status", Static)
        season_select = self.query_one("#season_select", Select)

        try:
            status.update("Loading seasons...")
            # Use the default competition_id to get category data
            data = KorisAPI.get_category("huki2526", self.current_category)

            # Extract seasons from the response
            if "category" in data and "seasons" in data["category"]:
                seasons_list = data["category"]["seasons"]
                self.seasons = {}
                season_options = []

                for season in seasons_list:
                    season_id = season["season_id"]
                    self.seasons[season_id] = season
                    # Only show the season name (e.g., "2025-2026")
                    season_options.append((season["season_name"], season_id))

                # Update the season select widget
                season_select.set_options(season_options)
                if season_options:
                    # Set the first season as default
                    first_season_id = seasons_list[0]["season_id"]
                    self.current_season = first_season_id
                    self.current_competition_id = seasons_list[0]["competition_id"]
                    season_select.value = first_season_id

                    # Auto-fetch matches for the first season
                    status.update(
                        f"Loaded {len(seasons_list)} seasons - Loading matches..."
                    )
                    self.fetch_matches()
                else:
                    status.update(f"Loaded {len(seasons_list)} seasons")
                    status.add_class("info")
            else:
                status.update("No seasons found")
                status.add_class("error")

        except Exception as e:
            status.update(f"Error loading seasons: {str(e)}")
            status.add_class("error")

    def fetch_matches(self) -> None:
        """Fetch and display matches"""
        status = self.query_one("#status", Static)

        if not self.current_season or not self.current_competition_id:
            status.update("Please select a season first")
            status.add_class("error")
            return

        try:
            status.update(f"Fetching matches for {self.current_season}...")

            # Track fetch time
            import time

            start_time = time.time()

            data = KorisAPI.get_matches(
                competition_id=self.current_competition_id,
                category_id=self.current_category,
            )
            self.current_data = data

            # Calculate fetch time in milliseconds
            self.last_fetch_time = int((time.time() - start_time) * 1000)

            # Clear matches data for saving
            self.matches_data = []

            # Add matches to data - the matches are directly under the "matches" key
            if "matches" in data and len(data["matches"]) > 0:
                matches = data["matches"]
                for match in matches:
                    # Team A is home, Team B is away
                    home_team = match.get(
                        "club_A_name", match.get("team_A_name", "N/A")
                    )
                    away_team = match.get(
                        "club_B_name", match.get("team_B_name", "N/A")
                    )
                    date = match.get("date", "N/A")
                    time_str = match.get("time", "N/A")
                    if time_str and time_str != "N/A" and len(time_str) >= 5:
                        time_str = time_str[:5]  # Show only HH:MM
                    match_status = match.get("status", "Scheduled")

                    # Get score - fs_A and fs_B are the final scores
                    home_score = match.get("fs_A", "-")
                    away_score = match.get("fs_B", "-")
                    if not home_score or home_score == "":
                        home_score = "-"
                    if not away_score or away_score == "":
                        away_score = "-"

                    # Determine if match has been played
                    is_played = home_score != "-" and away_score != "-"

                    # Store for saving and filtering
                    self.matches_data.append(
                        {
                            "Match ID": match.get("match_id", ""),
                            "Date": date,
                            "Time": time_str,
                            "Home Team": home_team,
                            "Home Team ID": match.get("team_A_id", ""),
                            "Home Score": home_score,
                            "Away Score": away_score,
                            "Away Team": away_team,
                            "Away Team ID": match.get("team_B_id", ""),
                            "Status": match_status,
                            "Venue": match.get("venue_name", "N/A"),
                            "Competition": match.get("competition_name", "N/A"),
                            "Category": match.get("category_name", "N/A"),
                            "Season": self.current_season,
                            "is_played": is_played,
                        }
                    )

                # Render matches with current filter
                self.render_matches()
            else:
                status.update(f"No matches found for season {self.current_season}")
                status.remove_class("info")
                status.add_class("error")

        except Exception as e:
            status.update(f"Error: {str(e)}")
            status.remove_class("info")
            status.add_class("error")

    def render_matches(self) -> None:
        """Render matches based on current filter"""
        status = self.query_one("#status", Static)
        table = self.query_one(DataTable)
        filter_select = self.query_one("#filter_select", Select)

        # Clear and set up table
        table.clear(columns=True)
        table.add_columns("Home Team", "Score", "Away Team", "Date", "Time")

        # Get current filter
        current_filter = (
            str(filter_select.value) if filter_select.value != Select.BLANK else "all"
        )

        # Filter matches
        filtered_matches = []
        for match in self.matches_data:
            if current_filter == "all":
                filtered_matches.append(match)
            elif current_filter == "played" and match["is_played"]:
                filtered_matches.append(match)
            elif current_filter == "upcoming" and not match["is_played"]:
                filtered_matches.append(match)

        # Add to table
        for match in filtered_matches:
            score = f"{match['Home Score']} - {match['Away Score']}"
            table.add_row(
                match["Home Team"],
                score,
                match["Away Team"],
                match["Date"],
                match["Time"],
            )

        # Update status with count and time
        total_matches = len(self.matches_data)
        filtered_count = len(filtered_matches)

        if current_filter == "all":
            status.update(
                f"Loaded {total_matches} matches for {self.current_season} in {self.last_fetch_time}ms"
            )
        else:
            filter_name = "played" if current_filter == "played" else "upcoming"
            status.update(
                f"Showing {filtered_count} {filter_name} of {total_matches} matches for {self.current_season} (loaded in {self.last_fetch_time}ms)"
            )

        status.remove_class("error")
        status.add_class("info")

    def save_data(self) -> None:
        """Save the current matches data to a file"""
        status = self.query_one("#status", Static)
        filter_select = self.query_one("#filter_select", Select)

        if not self.matches_data:
            status.update("No data to save. Fetch matches first.")
            status.add_class("error")
            return

        try:
            # Get current filter
            current_filter = (
                str(filter_select.value)
                if filter_select.value != Select.BLANK
                else "all"
            )

            # Filter matches for saving
            filtered_matches = []
            for match in self.matches_data:
                # Remove the is_played field before saving
                match_copy = {k: v for k, v in match.items() if k != "is_played"}

                if current_filter == "all":
                    filtered_matches.append(match_copy)
                elif current_filter == "played" and match["is_played"]:
                    filtered_matches.append(match_copy)
                elif current_filter == "upcoming" and not match["is_played"]:
                    filtered_matches.append(match_copy)

            # Generate filename
            category_name = self.categories.get(self.current_category, {}).get(
                "category_name", "category"
            )
            category_name = category_name.replace(" ", "_").replace("/", "_")
            season = (
                self.current_season.replace("-", "_")
                if self.current_season
                else "season"
            )
            filter_suffix = f"_{current_filter}" if current_filter != "all" else ""

            if self.save_format == "json":
                filename = f"matches_{category_name}_{season}{filter_suffix}.json"
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(filtered_matches, f, indent=2, ensure_ascii=False)
                status.update(f"Saved {len(filtered_matches)} matches to {filename}")

            elif self.save_format == "csv":
                filename = f"matches_{category_name}_{season}{filter_suffix}.csv"
                df = pd.DataFrame(filtered_matches)
                df.to_csv(filename, index=False, encoding="utf-8")
                status.update(f"Saved {len(filtered_matches)} matches to {filename}")

            elif self.save_format == "excel":
                filename = f"matches_{category_name}_{season}{filter_suffix}.xlsx"
                df = pd.DataFrame(filtered_matches)
                df.to_excel(filename, index=False, engine="openpyxl")
                status.update(f"Saved {len(filtered_matches)} matches to {filename}")

            status.remove_class("error")
            status.add_class("info")

        except Exception as e:
            status.update(f"Error saving data: {str(e)}")
            status.remove_class("info")
            status.add_class("error")


def run():
    """Entry point for the TUI application"""
    app = KorisApp()
    app.run()


if __name__ == "__main__":
    run()
