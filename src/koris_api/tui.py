import json
from pathlib import Path
from typing import Optional, cast
import pandas as pd
import requests
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Button, Static, Select, DataTable
from textual.containers import Container, Horizontal, VerticalScroll
from textual.binding import Binding
from textual.screen import Screen
from .basketfi_api import BasketFiAPI
from .genius_api import GeniusSportsAPI
from .boxscore_normalizer import normalize_boxscore
from .__init__ import (
    _augment_seasons_with_baskethotel,
    _extract_season_start_year,
    _fetch_baskethotel_schedule_game_ids,
    _fetch_baskethotel_team_map,
    _get_baskethotel_league_id,
    _get_baskethotel_session,
    _merge_baskethotel_match,
    _resolve_baskethotel_season_id,
)
from .baskethotel_api import BasketHotelAPI
from .__init__ import _augment_seasons_with_baskethotel


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

    def __init__(
        self,
        match_id: str,
        home_team: str,
        away_team: str,
        season_name: Optional[str] = None,
        category_id: Optional[str] = None,
        match_summary: Optional[dict] = None,
    ):
        super().__init__()
        self.match_id = match_id
        self.home_team = home_team
        self.away_team = away_team
        self.season_name = season_name
        self.category_id = category_id
        self.match_summary = match_summary or {}
        self.match_data: Optional[dict] = None
        self.boxscore_data: Optional[dict] = None
        self.boxscore_match_id: Optional[str] = None  # Genius Sports match ID
        self.is_historical = False

    def compose(self) -> ComposeResult:
        """Create the match view layout."""
        yield Header()
        with VerticalScroll(id="match_content"):
            yield Static(
                f"Loading {self.home_team} vs {self.away_team}...",
                id="match_info_display",
            )
            # Advanced box score section
            yield Static("", id="advanced_boxscore_header")
            yield Static("", id="advanced_boxscore_loading")
            yield Static("", id="advanced_match_totals_header")
            yield DataTable(id="advanced_match_totals_table")
            yield Static("", id="advanced_team_totals_header")
            yield DataTable(id="advanced_team_totals_table")
            yield Static("", id="advanced_home_team_header")
            yield DataTable(id="advanced_home_players_table")
            yield Static("", id="advanced_away_team_header")
            yield DataTable(id="advanced_away_players_table")
            with Horizontal():
                yield Button("Back", id="btn_back", variant="primary")
                yield Button(
                    "Back to Matches", id="btn_back_to_matches", variant="default"
                )
        yield Footer()

    def on_mount(self) -> None:
        """Fetch and display match data when screen is mounted."""
        season_year = _extract_season_start_year(self.season_name)
        self.is_historical = season_year is not None and season_year < 2022
        if self.match_summary.get("boxscore"):
            self.boxscore_data = self.match_summary.get("boxscore")
        self.load_match_data()
        if not self.boxscore_data:
            self.load_advanced_boxscore()

    def load_match_data(self) -> None:
        """Fetch and display match information."""
        display = self.query_one("#match_info_display", Static)

        try:
            display.update("Loading match data...")
            if self.is_historical:
                self.match_data = {
                    "club_A_name": self.match_summary.get("Home Team", self.home_team),
                    "club_B_name": self.match_summary.get("Away Team", self.away_team),
                    "date": self.match_summary.get("Date"),
                    "time": self.match_summary.get("Time"),
                    "venue_name": self.match_summary.get("Venue"),
                    "venue_city": "",
                    "competition_name": self.match_summary.get(
                        "Competition", self.season_name or "Historical Season"
                    ),
                    "category_name": self.match_summary.get("Category", ""),
                    "status": self.match_summary.get("Status", "Played"),
                    "fs_A": self.match_summary.get("Home Score"),
                    "fs_B": self.match_summary.get("Away Score"),
                    "lineups": [],
                }
                self.render_match_info()
            else:
                data = BasketFiAPI.get_match(self.match_id)

                if "match" in data:
                    self.match_data = data["match"]
                    # Extract Genius Sports match ID if available
                    if self.match_data and "match_external_id" in self.match_data:
                        self.boxscore_match_id = self.match_data["match_external_id"]
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

    def load_advanced_boxscore(self) -> None:
        """Load advanced box score data in the background using a worker."""
        # Show loading indicator
        loading = self.query_one("#advanced_boxscore_loading", Static)
        header = self.query_one("#advanced_boxscore_header", Static)
        if self.boxscore_data:
            loading.update("[dim]Boxscore already loaded.[/dim]")
            return
        source_label = "BasketHotel" if self.is_historical else "Genius Sports"
        header.update("")
        loading.update("[dim]Loading advanced statistics...[/dim]")

        # Run the fetch in a worker to avoid blocking the UI
        self.run_worker(self._fetch_boxscore_worker, exclusive=False, thread=True)

    def _fetch_boxscore_worker(self) -> dict | None:
        """Worker function to fetch box score data."""
        try:
            if self.is_historical:
                if not self.season_name or not self.category_id:
                    return {
                        "error": "Season or category not available for BasketHotel match"
                    }
                league_id = _get_baskethotel_league_id(str(self.category_id))
                resolved_season_id = (
                    _resolve_baskethotel_season_id(self.season_name, league_id)
                    or self.season_name
                )
                session = _get_baskethotel_session()
                boxscore_data = BasketHotelAPI().fetch_boxscore_data(
                    str(self.match_id),
                    season_id=str(resolved_season_id),
                    league_id=str(league_id),
                    session=session,
                )
                normalized = normalize_boxscore(boxscore_data, source="baskethotel")
                return {"data": normalized}

            if not self.boxscore_match_id:
                return {"error": "Genius Sports match ID not available for this match"}

            # Fetch the box score data
            boxscore_data = GeniusSportsAPI.get_match_boxscore(
                str(self.boxscore_match_id)
            )
            normalized = normalize_boxscore(boxscore_data, source="genius")
            return {"data": normalized}

        except Exception as e:
            return {"error": str(e)}

    def on_worker_state_changed(self, event) -> None:
        """Handle worker state changes."""
        if event.worker.name == "_fetch_boxscore_worker":
            if event.state.name == "SUCCESS":
                result = event.worker.result
                if result and "data" in result:
                    self._update_boxscore_display(result["data"])
                elif result and "error" in result:
                    self._update_boxscore_error(result["error"])
            elif event.state.name == "ERROR":
                self._update_boxscore_error("Failed to load advanced statistics")

    def _update_boxscore_error(self, error_msg: str) -> None:
        """Update the UI when box score loading fails."""
        loading = self.query_one("#advanced_boxscore_loading", Static)
        loading.update(
            f"[dim red]Advanced statistics not available: {error_msg}[/dim red]"
        )

    def _update_boxscore_display(self, boxscore_data: dict) -> None:
        """Update the UI with fetched box score data."""
        self.boxscore_data = boxscore_data

        # Clear loading message
        loading = self.query_one("#advanced_boxscore_loading", Static)
        loading.update("")

        # Render the advanced box score
        self.render_advanced_boxscore()

    def render_advanced_boxscore(self) -> None:
        """Render the advanced box score tables."""
        if not self.boxscore_data or "teams" not in self.boxscore_data:
            return

        teams = self.boxscore_data["teams"]
        if len(teams) < 2:
            return

        match_totals = self.boxscore_data.get("match_totals", {})
        self._render_match_totals(match_totals)
        self._render_team_totals(teams)

        # Render home team (first team in the list)
        home_team = teams[0]
        home_header = self.query_one("#advanced_home_team_header", Static)
        home_header.update(
            f"\n[bold cyan]{home_team.get('team_name', 'Home Team')} - Player Statistics[/bold cyan]"
        )

        home_table = self.query_one("#advanced_home_players_table", DataTable)
        home_table.clear(columns=True)

        # Add columns for advanced stats
        home_table.add_column("#", width=4)
        home_table.add_column("Player", width=20)
        home_table.add_column("MIN", width=6)
        home_table.add_column("PTS", width=5)
        home_table.add_column("2PM-A", width=8)
        home_table.add_column("3PM-A", width=8)
        home_table.add_column("FTM-A", width=8)
        home_table.add_column("2P%", width=6)
        home_table.add_column("3P%", width=6)
        home_table.add_column("FT%", width=6)
        home_table.add_column("OFF", width=5)
        home_table.add_column("DEF", width=5)
        home_table.add_column("REB", width=5)
        home_table.add_column("AST", width=5)
        home_table.add_column("STL", width=5)
        home_table.add_column("BLK", width=5)
        home_table.add_column("TO", width=5)
        home_table.add_column("PF", width=5)
        home_table.add_column("+/-", width=5)
        home_table.add_column("IDX", width=5)

        home_table.show_header = True
        home_table.zebra_stripes = True
        home_table.cursor_type = "none"

        # Add player rows
        for player in home_team.get("players", []):
            mins_display = self._format_minutes(player.get("Minutes"))
            fg2_pct = self._format_pct(player.get("2P%"))
            fg3_pct = self._format_pct(player.get("3P%"))
            ft_pct = self._format_pct(player.get("FT%"))

            home_table.add_row(
                str(player.get("player_number", "-")),
                str(player.get("player", "Unknown"))[:19],  # Truncate long names
                mins_display,
                str(player.get("Points", 0)),
                f"{player.get('2PM', 0)}-{player.get('2PA', 0)}",
                f"{player.get('3PM', 0)}-{player.get('3PA', 0)}",
                f"{player.get('FTM', 0)}-{player.get('FTA', 0)}",
                fg2_pct,
                fg3_pct,
                ft_pct,
                str(player.get("OFF", 0)),
                str(player.get("DEF", 0)),
                str(player.get("REB", 0)),
                str(player.get("AST", 0)),
                str(player.get("STL", 0)),
                str(player.get("BLK", 0)),
                str(player.get("TO", 0)),
                str(player.get("PF", 0)),
                str(player.get("+/-", 0)),
                str(player.get("Index", 0)),
            )

        # Show coaching staff
        if "coaches" in home_team and home_team["coaches"]:
            coaches = home_team["coaches"]
            coach_text = f"  [dim]Head Coach: {coaches.get('head_coach', 'N/A')}"
            if coaches.get("assistant_coach"):
                coach_text += f" | Assistant: {coaches.get('assistant_coach')}[/dim]"
            else:
                coach_text += "[/dim]"
            home_header.update(
                f"\n[bold cyan]{home_team.get('team_name', 'Home Team')} - Player Statistics[/bold cyan]\n{coach_text}"
            )

        # Render away team (second team in the list)
        away_team = teams[1]
        away_header = self.query_one("#advanced_away_team_header", Static)
        away_header.update(
            f"\n[bold cyan]{away_team.get('team_name', 'Away Team')} - Player Statistics[/bold cyan]"
        )

        away_table = self.query_one("#advanced_away_players_table", DataTable)
        away_table.clear(columns=True)

        # Add columns for advanced stats (same as home)
        away_table.add_column("#", width=4)
        away_table.add_column("Player", width=20)
        away_table.add_column("MIN", width=6)
        away_table.add_column("PTS", width=5)
        away_table.add_column("2PM-A", width=8)
        away_table.add_column("3PM-A", width=8)
        away_table.add_column("FTM-A", width=8)
        away_table.add_column("2P%", width=6)
        away_table.add_column("3P%", width=6)
        away_table.add_column("FT%", width=6)
        away_table.add_column("OFF", width=5)
        away_table.add_column("DEF", width=5)
        away_table.add_column("REB", width=5)
        away_table.add_column("AST", width=5)
        away_table.add_column("STL", width=5)
        away_table.add_column("BLK", width=5)
        away_table.add_column("TO", width=5)
        away_table.add_column("PF", width=5)
        away_table.add_column("+/-", width=5)
        away_table.add_column("IDX", width=5)

        away_table.show_header = True
        away_table.zebra_stripes = True
        away_table.cursor_type = "none"

        # Add player rows
        for player in away_team.get("players", []):
            mins_display = self._format_minutes(player.get("Minutes"))
            fg2_pct = self._format_pct(player.get("2P%"))
            fg3_pct = self._format_pct(player.get("3P%"))
            ft_pct = self._format_pct(player.get("FT%"))

            away_table.add_row(
                str(player.get("player_number", "-")),
                str(player.get("player", "Unknown"))[:19],  # Truncate long names
                mins_display,
                str(player.get("Points", 0)),
                f"{player.get('2PM', 0)}-{player.get('2PA', 0)}",
                f"{player.get('3PM', 0)}-{player.get('3PA', 0)}",
                f"{player.get('FTM', 0)}-{player.get('FTA', 0)}",
                fg2_pct,
                fg3_pct,
                ft_pct,
                str(player.get("OFF", 0)),
                str(player.get("DEF", 0)),
                str(player.get("REB", 0)),
                str(player.get("AST", 0)),
                str(player.get("STL", 0)),
                str(player.get("BLK", 0)),
                str(player.get("TO", 0)),
                str(player.get("PF", 0)),
                str(player.get("+/-", 0)),
                str(player.get("Index", 0)),
            )

        # Show coaching staff
        if "coaches" in away_team and away_team["coaches"]:
            coaches = away_team["coaches"]
            coach_text = f"  [dim]Head Coach: {coaches.get('head_coach', 'N/A')}"
            if coaches.get("assistant_coach"):
                coach_text += f" | Assistant: {coaches.get('assistant_coach')}[/dim]"
            else:
                coach_text += "[/dim]"
            away_header.update(
                f"\n[bold cyan]{away_team.get('team_name', 'Away Team')} - Player Statistics[/bold cyan]\n{coach_text}"
            )


    def _format_minutes(self, value: object) -> str:
        if value is None:
            return "-"
        return str(value)

    def _format_pct(self, value: object) -> str:
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return "0.0"

    def _render_match_totals(self, totals: dict) -> None:
        header = self.query_one("#advanced_match_totals_header", Static)
        table = self.query_one("#advanced_match_totals_table", DataTable)
        header.update("\n[bold cyan]Match Totals[/bold cyan]")
        table.clear(columns=True)
        table.add_columns(
            "PTS",
            "2PM-A",
            "2P%",
            "3PM-A",
            "3P%",
            "FTM-A",
            "FT%",
            "OFF",
            "DEF",
            "REB",
            "AST",
            "STL",
            "BLK",
            "TO",
            "PF",
            "IDX",
        )
        table.show_header = True
        table.zebra_stripes = True
        table.cursor_type = "none"
        table.add_row(
            str(totals.get("Points", 0)),
            f"{totals.get('2PM', 0)}-{totals.get('2PA', 0)}",
            self._format_pct(totals.get("2P%")),
            f"{totals.get('3PM', 0)}-{totals.get('3PA', 0)}",
            self._format_pct(totals.get("3P%")),
            f"{totals.get('FTM', 0)}-{totals.get('FTA', 0)}",
            self._format_pct(totals.get("FT%")),
            str(totals.get("OFF", 0)),
            str(totals.get("DEF", 0)),
            str(totals.get("REB", 0)),
            str(totals.get("AST", 0)),
            str(totals.get("STL", 0)),
            str(totals.get("BLK", 0)),
            str(totals.get("TO", 0)),
            str(totals.get("PF", 0)),
            str(totals.get("Index", 0)),
        )

    def _render_team_totals(self, teams: list) -> None:
        header = self.query_one("#advanced_team_totals_header", Static)
        table = self.query_one("#advanced_team_totals_table", DataTable)
        header.update("\n[bold cyan]Team Totals[/bold cyan]")
        table.clear(columns=True)
        table.add_columns(
            "Team",
            "PTS",
            "2PM-A",
            "2P%",
            "3PM-A",
            "3P%",
            "FTM-A",
            "FT%",
            "OFF",
            "DEF",
            "REB",
            "AST",
            "STL",
            "BLK",
            "TO",
            "PF",
            "IDX",
        )
        table.show_header = True
        table.zebra_stripes = True
        table.cursor_type = "none"
        for team in teams:
            totals = team.get("totals", {})
            table.add_row(
                str(team.get("team_name", "Team")),
                str(totals.get("Points", 0)),
                f"{totals.get('2PM', 0)}-{totals.get('2PA', 0)}",
                self._format_pct(totals.get("2P%")),
                f"{totals.get('3PM', 0)}-{totals.get('3PA', 0)}",
                self._format_pct(totals.get("3P%")),
                f"{totals.get('FTM', 0)}-{totals.get('FTA', 0)}",
                self._format_pct(totals.get("FT%")),
                str(totals.get("OFF", 0)),
                str(totals.get("DEF", 0)),
                str(totals.get("REB", 0)),
                str(totals.get("AST", 0)),
                str(totals.get("STL", 0)),
                str(totals.get("BLK", 0)),
                str(totals.get("TO", 0)),
                str(totals.get("PF", 0)),
                str(totals.get("Index", 0)),
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
            data = BasketFiAPI.get_team(self.team_id)

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
            matches_data = BasketFiAPI.get_matches(team_id=str(self.team_id))

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

    #loading_bar {
        height: 1;
        padding: 0 1;
        color: $primary;
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
        self.current_season_name = None
        self.save_format = "json"  # Default save format
        self.include_boxscores = False
        self.matches_data = []  # Store matches for saving
        self.show_upcoming = True  # Show upcoming games by default
        self.last_fetch_time = 0  # Store last fetch duration
        self._historical_fetch_context = None
        self._historical_fetch_start = None
        self._boxscore_save_context = None
        self._historical_matches_cache = {}
        self._active_fetch_id = 0
        self._matches_table_initialized = False

    def load_categories(self) -> dict:
        """Load categories from JSON file"""
        categories_path = Path(__file__).parent.parent.parent / "categories.json"
        if categories_path.exists():
            with open(categories_path) as f:
                return cast(dict, json.load(f))
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
                yield Select(
                    options=[
                        ("Save Matches Only", "matches"),
                        ("Include Boxscores", "boxscores"),
                    ],
                    value="matches",
                    id="boxscore_select",
                    prompt="Save Boxscores",
                )
                yield Button("Save Data", id="btn_save", variant="success")

        yield Static("Ready - Select a category to load seasons", id="status")
        yield Static("", id="loading_bar")
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
                self.current_season_name = season_data.get("season_name")
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
        elif event.select.id == "boxscore_select":
            if event.value == Select.BLANK or not event.value:
                return
            self.include_boxscores = str(event.value) == "boxscores"

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
            season_name = self.current_season_name or self.current_season
            season_year = _extract_season_start_year(season_name)
            is_historical = season_year is not None and season_year < 2022

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
                                season_name=season_name,
                                category_id=self.current_category,
                                match_summary=match if is_historical else None,
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
            data = BasketFiAPI.get_category("huki2526", self.current_category)

            # Extract seasons from the response
            if "category" in data and "seasons" in data["category"]:
                seasons_list = data["category"]["seasons"]
                seasons_list = _augment_seasons_with_baskethotel(
                    seasons_list, self.current_category
                )
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
                    self.current_season_name = seasons_list[0].get("season_name")
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

    def _format_progress_bar(self, current: int, total: int, width: int = 30) -> str:
        if total <= 0:
            return ""
        filled = int((current / total) * width)
        return f"[{'#' * filled}{'.' * (width - filled)}] {current}/{total}"

    def _update_loading_bar(
        self, current: int, total: int, message: Optional[str] = None
    ) -> None:
        if message:
            status = self.query_one("#status", Static)
            status.update(message)
        loading = self.query_one("#loading_bar", Static)
        if total:
            loading.update(self._format_progress_bar(current, total))
        else:
            loading.update("Loading...")

    def _clear_loading_bar(self) -> None:
        loading = self.query_one("#loading_bar", Static)
        loading.update("")

    def _init_matches_table(self) -> None:
        table = self.query_one(DataTable)
        if self._matches_table_initialized:
            return
        table.clear(columns=True)
        table.add_columns("Home Team", "Score", "Away Team", "Date", "Time")
        self._matches_table_initialized = True

    def _current_filter(self) -> str:
        filter_select = self.query_one("#filter_select", Select)
        return (
            str(filter_select.value) if filter_select.value != Select.BLANK else "all"
        )

    def _match_passes_filter(self, match_row: dict, filter_value: str) -> bool:
        if filter_value == "all":
            return True
        if filter_value == "played":
            return match_row["is_played"]
        return not match_row["is_played"]

    def _append_match_row(self, match_row: dict) -> None:
        self._init_matches_table()
        current_filter = self._current_filter()
        if not self._match_passes_filter(match_row, current_filter):
            return
        table = self.query_one(DataTable)
        score = f"{match_row['Home Score']} - {match_row['Away Score']}"
        table.add_row(
            match_row["Home Team"],
            score,
            match_row["Away Team"],
            match_row["Date"],
            match_row["Time"],
        )

    def _update_status_counts(self, season_name: str) -> None:
        status = self.query_one("#status", Static)
        total_matches = len(self.matches_data)
        current_filter = self._current_filter()
        filtered_count = sum(
            1 for match in self.matches_data if self._match_passes_filter(match, current_filter)
        )
        if current_filter == "all":
            status.update(
                f"Loaded {total_matches} matches for {season_name} in {self.last_fetch_time}ms"
            )
        else:
            filter_name = "played" if current_filter == "played" else "upcoming"
            status.update(
                f"Showing {filtered_count} {filter_name} of {total_matches} matches for {season_name} (loaded in {self.last_fetch_time}ms)"
            )
        status.remove_class("error")
        status.add_class("info")

    def _start_historical_fetch(self, season_data: dict, season_name: str) -> None:
        cache_key = f"{self.current_category}:{season_name}"
        cached = self._historical_matches_cache.get(cache_key)
        if cached is not None:
            self._clear_loading_bar()
            self._process_matches_payload({"matches": cached}, season_name, True)
            return

        self._matches_table_initialized = False
        self._init_matches_table()
        self._historical_fetch_context = {
            "season_data": season_data,
            "season_name": season_name,
            "category_id": self.current_category,
            "category_name": self.categories[self.current_category]["category_name"],
        }
        import time

        self._historical_fetch_start = time.time()
        self._active_fetch_id += 1
        self._historical_fetch_context["fetch_id"] = self._active_fetch_id
        self.matches_data = []
        self._update_loading_bar(0, 0, f"Loading historical season {season_name}...")
        self.run_worker(
            self._fetch_historical_matches_worker, exclusive=True, thread=True
        )

    def _fetch_historical_matches_worker(self) -> dict:
        context = self._historical_fetch_context or {}
        season_data = context.get("season_data", {})
        season_name = context.get("season_name", "")
        category_id = context.get("category_id", "")
        category_name = context.get("category_name", "")
        fetch_id = context.get("fetch_id")

        league_id = _get_baskethotel_league_id(str(category_id))
        resolved_season_id = (
            _resolve_baskethotel_season_id(season_name, league_id)
            or season_data.get("season_id")
            or season_name
        )
        team_map = _fetch_baskethotel_team_map(str(resolved_season_id), str(league_id))
        game_ids = _fetch_baskethotel_schedule_game_ids(
            str(resolved_season_id), str(league_id), False
        )

        total = len(game_ids)
        self.call_from_thread(
            self._update_loading_bar,
            0,
            total,
            f"Loading historical season {season_name}...",
        )

        client = BasketHotelAPI()
        session = _get_baskethotel_session()
        processed_matches = []

        for idx, game_id in enumerate(game_ids, start=1):
            try:
                game_data = client.fetch_game_data(
                    str(game_id),
                    season_id=str(resolved_season_id),
                    league_id=str(league_id),
                    session=session,
                )
                base_match = {
                    "match_id": str(game_id),
                    "match_external_id": None,
                    "date": None,
                    "time": None,
                    "home_team": None,
                    "home_team_id": None,
                    "away_team": None,
                    "away_team_id": None,
                    "home_score": None,
                    "away_score": None,
                    "status": None,
                    "venue": None,
                    "competition": season_name,
                    "category": category_name,
                    "season": season_name,
                }
                merged = _merge_baskethotel_match(base_match, game_data)
                home_name = merged.get("home_team")
                away_name = merged.get("away_team")
                if home_name and not merged.get("home_team_id"):
                    merged["home_team_id"] = team_map.get(home_name)
                if away_name and not merged.get("away_team_id"):
                    merged["away_team_id"] = team_map.get(away_name)
                processed_matches.append(merged)
                self.call_from_thread(
                    self._on_historical_match_loaded,
                    fetch_id,
                    merged,
                    idx,
                    total,
                    season_name,
                )
            except Exception:
                pass

            self.call_from_thread(self._update_loading_bar, idx, total)

        return {
            "matches": processed_matches,
            "season_name": season_name,
            "fetch_id": fetch_id,
        }

    def _on_historical_match_loaded(
        self,
        fetch_id: int,
        match: dict,
        current: int,
        total: int,
        season_name: str,
    ) -> None:
        if fetch_id != self._active_fetch_id:
            return
        match_row = self._append_match(match, season_name, True)
        self._append_match_row(match_row)
        self._update_loading_bar(
            current, total, f"Loading historical season {season_name}..."
        )

    def _append_match(
        self, match: dict, season_name: str, is_historical: bool
    ) -> dict:
        if is_historical:
            home_team = match.get("home_team", "N/A")
            away_team = match.get("away_team", "N/A")
            date = match.get("date", "N/A")
            time_str = match.get("time", "N/A")
            match_status = match.get("status", "Scheduled")
            home_score = match.get("home_score", "-")
            away_score = match.get("away_score", "-")
            team_a_id = match.get("home_team_id", "")
            team_b_id = match.get("away_team_id", "")
            match_id = match.get("match_id", "")
            match_external_id = ""
            venue = match.get("venue", "N/A")
            competition = match.get("competition", "N/A")
            category = match.get("category", "N/A")
        else:
            home_team = match.get("club_A_name", match.get("team_A_name", "N/A"))
            away_team = match.get("club_B_name", match.get("team_B_name", "N/A"))
            date = match.get("date", "N/A")
            time_str = match.get("time", "N/A")
            match_status = match.get("status", "Scheduled")
            home_score = match.get("fs_A", "-")
            away_score = match.get("fs_B", "-")
            team_a_id = match.get("team_A_id", "")
            team_b_id = match.get("team_B_id", "")
            match_id = match.get("match_id", "")
            match_external_id = match.get("match_external_id", "")
            venue = match.get("venue_name", "N/A")
            competition = match.get("competition_name", "N/A")
            category = match.get("category_name", "N/A")

        if time_str and time_str != "N/A" and len(time_str) >= 5:
            time_str = time_str[:5]
        if not home_score or home_score == "":
            home_score = "-"
        if not away_score or away_score == "":
            away_score = "-"

        is_played = home_score != "-" and away_score != "-"

        match_row = {
            "Match ID": match_id,
            "Match External ID": match_external_id,
            "Date": date,
            "Time": time_str,
            "Home Team": home_team,
            "Home Team ID": team_a_id,
            "Home Score": home_score,
            "Away Score": away_score,
            "Away Team": away_team,
            "Away Team ID": team_b_id,
            "Status": match_status,
            "Venue": venue,
            "Competition": competition,
            "Category": category,
            "Season": season_name,
            "is_played": is_played,
        }
        self.matches_data.append(
            {
                "Match ID": match_id,
                "Match External ID": match_external_id,
                "Date": date,
                "Time": time_str,
                "Home Team": home_team,
                "Home Team ID": team_a_id,
                "Home Score": home_score,
                "Away Score": away_score,
                "Away Team": away_team,
                "Away Team ID": team_b_id,
                "Status": match_status,
                "Venue": venue,
                "Competition": competition,
                "Category": category,
                "Season": season_name,
                "is_played": is_played,
            }
        )
        return match_row

    def _process_matches_payload(
        self, data: dict, season_name: str, is_historical: bool
    ) -> None:
        status = self.query_one("#status", Static)
        self.current_data = data
        self.matches_data = []

        if "matches" in data and len(data["matches"]) > 0:
            matches = data["matches"]
            for match in matches:
                self._append_match(match, season_name, is_historical)

            self.render_matches()
        else:
            status.update(f"No matches found for season {season_name}")
            status.remove_class("info")
            status.add_class("error")

    def fetch_matches(self) -> None:
        """Fetch and display matches"""
        status = self.query_one("#status", Static)

        if not self.current_season or not self.current_competition_id:
            status.update("Please select a season first")
            status.add_class("error")
            return

        try:
            season_data = self.seasons.get(str(self.current_season), {})
            season_name = (
                season_data.get("season_name")
                or self.current_season_name
                or self.current_season
            )

            season_year = _extract_season_start_year(season_name)
            is_historical = season_year is not None and season_year < 2022

            if is_historical and season_data.get("competition_id") == season_name:
                self._start_historical_fetch(season_data, season_name)
                return

            status.update(f"Fetching matches for {season_name}...")

            # Track fetch time
            import time

            start_time = time.time()

            data = BasketFiAPI.get_matches(
                competition_id=self.current_competition_id,
                category_id=self.current_category,
            )
            self.current_data = data

            # Calculate fetch time in milliseconds
            self.last_fetch_time = int((time.time() - start_time) * 1000)

            self._clear_loading_bar()
            self._process_matches_payload(data, season_name, False)

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
        self._matches_table_initialized = False
        self._init_matches_table()

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
        season_name = self.current_season_name or self.current_season

        self._update_status_counts(season_name)

    def save_data(self) -> None:
        """Save the current matches data to a file"""
        status = self.query_one("#status", Static)
        filter_select = self.query_one("#filter_select", Select)

        if not self.matches_data:
            status.update("No data to save. Fetch matches first.")
            status.add_class("error")
            return

        try:
            if self.include_boxscores and self.save_format != "json":
                status.update("Boxscore save is only available for JSON output.")
                status.add_class("error")
                return

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
                (self.current_season_name or self.current_season).replace("-", "_")
                if (self.current_season_name or self.current_season)
                else "season"
            )
            filter_suffix = f"_{current_filter}" if current_filter != "all" else ""

            if self.save_format == "json":
                filename = f"matches_{category_name}_{season}{filter_suffix}.json"
                if self.include_boxscores:
                    self._start_boxscore_save(filtered_matches, filename)
                    return
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

    def _start_boxscore_save(self, matches: list, filename: str) -> None:
        self._boxscore_save_context = {
            "matches": matches,
            "filename": filename,
            "season_name": self.current_season_name or self.current_season,
            "category_id": self.current_category,
        }
        self._update_loading_bar(
            0, len(matches), "Downloading boxscores and saving..."
        )
        self.run_worker(self._save_boxscores_worker, exclusive=True, thread=True)

    def _save_boxscores_worker(self) -> dict:
        context = self._boxscore_save_context or {}
        matches = context.get("matches", [])
        filename = context.get("filename", "matches.json")
        season_name = context.get("season_name")
        category_id = context.get("category_id")

        season_year = _extract_season_start_year(season_name)
        is_historical = season_year is not None and season_year < 2022

        results = []
        total = len(matches)
        for idx, match in enumerate(matches, start=1):
            match_id = match.get("Match ID")
            external_id = match.get("Match External ID")
            boxscore = None
            source = None

            try:
                if is_historical:
                    league_id = _get_baskethotel_league_id(str(category_id))
                    resolved_season_id = (
                        _resolve_baskethotel_season_id(season_name, league_id)
                        or season_name
                    )
                    session = _get_baskethotel_session()
                    boxscore_raw = BasketHotelAPI().fetch_boxscore_data(
                        str(match_id),
                        season_id=str(resolved_season_id),
                        league_id=str(league_id),
                        session=session,
                    )
                    boxscore = normalize_boxscore(boxscore_raw, source="baskethotel")
                    source = "baskethotel"
                else:
                    if not external_id:
                        match_detail = BasketFiAPI.get_match(str(match_id))
                        external_id = (
                            match_detail.get("match", {}).get("match_external_id")
                        )
                    if external_id:
                        boxscore_raw = GeniusSportsAPI.get_match_boxscore(
                            str(external_id)
                        )
                        boxscore = normalize_boxscore(boxscore_raw, source="genius")
                        source = "genius"
            except Exception:
                boxscore = None

            enriched = dict(match)
            if boxscore:
                enriched["boxscore_source"] = source
                enriched["boxscore"] = boxscore
            results.append(enriched)

            self.call_from_thread(self._update_loading_bar, idx, total)

        Path(filename).write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {"filename": filename, "count": len(results)}

    def on_worker_state_changed(self, event) -> None:
        if event.worker.name == "_fetch_historical_matches_worker":
            if event.state.name == "SUCCESS":
                result = event.worker.result or {}
                matches = result.get("matches", [])
                season_name = result.get("season_name") or self.current_season_name
                fetch_id = result.get("fetch_id")
                if fetch_id != self._active_fetch_id:
                    return
                if self._historical_fetch_start:
                    import time

                    self.last_fetch_time = int(
                        (time.time() - self._historical_fetch_start) * 1000
                    )
                    self._historical_fetch_start = None
                self._clear_loading_bar()
                cache_key = f"{self.current_category}:{season_name}"
                self._historical_matches_cache[cache_key] = matches
                self.current_data = {"matches": matches}
                self._update_status_counts(str(season_name))
            elif event.state.name == "ERROR":
                status = self.query_one("#status", Static)
                self._clear_loading_bar()
                status.update("Error loading historical season")
                status.add_class("error")
        elif event.worker.name == "_save_boxscores_worker":
            status = self.query_one("#status", Static)
            if event.state.name == "SUCCESS":
                result = event.worker.result or {}
                self._clear_loading_bar()
                status.update(
                    f"Saved {result.get('count', 0)} matches to {result.get('filename')}"
                )
                status.remove_class("error")
                status.add_class("info")
            elif event.state.name == "ERROR":
                self._clear_loading_bar()
                status.update("Error saving boxscores")
                status.add_class("error")


def run():
    """Entry point for the TUI application"""
    app = KorisApp()
    app.run()


if __name__ == "__main__":
    run()
