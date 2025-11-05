"""Fast unit tests using fixtures instead of live API calls."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from koris_api.basketfi_api import BasketFiAPI
from koris_api.basketfi_parser import BasketFiParser
from koris_api.genius_api import GeniusSportsAPI
from koris_api.genius_parser import GeniusSportsParser


# =============================================================================
# FIXTURES - Load test data from files
# =============================================================================


@pytest.fixture
def fixtures_dir():
    """Return the path to the fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def basketfi_matches(fixtures_dir):
    """Load basketfi matches fixture."""
    with open(fixtures_dir / "basketfi" / "matches.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def basketfi_match_detail(fixtures_dir):
    """Load basketfi match detail fixture."""
    with open(
        fixtures_dir / "basketfi" / "match_detail.json", "r", encoding="utf-8"
    ) as f:
        return json.load(f)


@pytest.fixture
def basketfi_team(fixtures_dir):
    """Load basketfi team fixture."""
    with open(fixtures_dir / "basketfi" / "team.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def basketfi_category(fixtures_dir):
    """Load basketfi category fixture."""
    with open(fixtures_dir / "basketfi" / "category.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def genius_boxscore(fixtures_dir):
    """Load genius sports boxscore fixture."""
    with open(
        fixtures_dir / "genius_sports" / "boxscore.json", "r", encoding="utf-8"
    ) as f:
        return json.load(f)


@pytest.fixture
def genius_boxscore_html():
    """Load the HTML version of genius boxscore for parsing tests."""
    example_file = (
        Path(__file__).parent.parent / "example_responses" / "genius-box-score.html"
    )
    with open(example_file, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def genius_team_statistics_html():
    """Load the HTML version of genius team statistics for parsing tests."""
    example_file = (
        Path(__file__).parent.parent / "example_responses" / "genius-team-players.html"
    )
    with open(example_file, "r", encoding="utf-8") as f:
        return f.read()


# =============================================================================
# PARSING TESTS - Test parsers with fixture data (no API calls)
# =============================================================================


def test_boxscore_parsing_from_html(genius_boxscore_html):
    """Test parsing box score from HTML file."""
    result = GeniusSportsParser.parse_boxscore_html(genius_boxscore_html)

    # Verify structure
    assert "match_info" in result
    assert "teams" in result
    assert len(result["teams"]) == 2

    # Verify match info
    match_info = result["match_info"]
    assert match_info["home_team"] == "Jyväskylä Basketball Academy"
    assert match_info["away_team"] == "ACO Basket"
    assert match_info["home_score"] == 63
    assert match_info["away_score"] == 78
    assert match_info["status"] == "Final"

    # Verify player data
    for team in result["teams"]:
        assert len(team["players"]) > 0, "Team should have players"
        player = team["players"][0]

        # All 23 fields should be present
        expected_fields = [
            "Shirt Number",
            "Player",
            "Minutes",
            "Points",
            "2 Points Made",
            "2 Points Attempted",
            "2 Points Percentage",
            "3 Points Made",
            "3 Points Atttempted",
            "3 Point Percentage",
            "Free Throws Made",
            "Free Throws Attempted",
            "Free Throw Percentage",
            "Offensive Rebounds",
            "Defensive Rebounds",
            "Total Rebounds",
            "Assists",
            "Steals",
            "Turnovers",
            "Blocks",
            "Personal Foul",
            "Plus/Minus",
            "Index of Success",
        ]
        for field in expected_fields:
            assert field in player, f"Player should have {field} field"


def test_team_statistics_parsing_from_html(genius_team_statistics_html):
    """Test parsing team statistics from HTML file."""
    result = GeniusSportsParser.parse_team_statistics_page(genius_team_statistics_html)

    # Verify structure
    assert "team_name" in result
    assert "team_location" in result
    assert "averages" in result
    assert "shooting" in result
    assert "totals" in result

    # Verify team info
    assert result["team_name"] == "ACO Basket"
    assert result["team_location"] == "Oulu"

    # Verify all three statistical categories have data
    assert len(result["averages"]) == 12, "Should have 12 players in averages"
    assert len(result["shooting"]) == 12, "Should have 12 players in shooting stats"
    assert len(result["totals"]) == 12, "Should have 12 players in totals"

    # Verify averages structure
    player_avg = result["averages"][0]
    assert "player_id" in player_avg
    assert "player_name" in player_avg
    expected_avg_fields = [
        "Games",
        "Games started",
        "Average minutes",
        "Average points",
        "Average offensive rebounds",
        "Average defensive rebounds",
        "Average total rebounds",
        "Average assists",
        "Average steals",
        "Average blocks",
        "Average personal fouls",
        "Average turnovers",
        "Average +/-",
    ]
    for field in expected_avg_fields:
        assert field in player_avg, f"Player averages should have {field} field"

    # Verify shooting structure
    player_shoot = result["shooting"][0]
    assert "player_id" in player_shoot
    assert "player_name" in player_shoot
    expected_shoot_fields = [
        "2 Points made",
        "2 Points attempted",
        "2 Points percentage",
        "3 Points made",
        "3 Points attempted",
        "3 Point percentage",
        "Free throws made",
        "Free throws attempted",
        "Free throw percentage",
    ]
    for field in expected_shoot_fields:
        assert field in player_shoot, f"Player shooting should have {field} field"

    # Verify totals structure
    player_tot = result["totals"][0]
    assert "player_id" in player_tot
    assert "player_name" in player_tot
    expected_tot_fields = [
        "Games",
        "Minutes",
        "Points",
        "Offensive rebounds",
        "Defensive rebounds",
        "Total rebounds",
        "Assists",
        "Steals",
        "Blocks",
        "Personal fouls",
        "Plus/minus",
        "Index of success",
    ]
    for field in expected_tot_fields:
        assert field in player_tot, f"Player totals should have {field} field"

    # Verify data types are correct
    assert isinstance(player_avg["Games"], int)
    assert isinstance(player_avg["Average points"], float)
    assert isinstance(player_shoot["2 Points made"], int)
    assert isinstance(player_shoot["2 Points percentage"], float)
    assert isinstance(player_tot["Points"], int)
    assert isinstance(player_tot["Minutes"], float)  # Time in decimal minutes


def test_basketfi_matches_parsing(basketfi_matches):
    """Test parsing basket.fi matches data."""
    matches = BasketFiParser.extract_matches(basketfi_matches)
    assert len(matches) > 0, "Should have matches"

    # Check that status values are valid
    for match in matches:
        status = match.get("status")
        assert status in ["Played", "Fixture"], (
            f"Status should be 'Played' or 'Fixture', got: {status}"
        )


def test_basketfi_match_detail_parsing(basketfi_match_detail):
    """Test parsing detailed match data."""
    assert "match" in basketfi_match_detail
    match = basketfi_match_detail["match"]

    # Verify basic match structure
    assert "match_id" in match
    assert "club_A_name" in match
    assert "club_B_name" in match
    assert "status" in match

    # Verify lineups exist
    assert "lineups" in match
    assert len(match["lineups"]) > 0


def test_basketfi_team_parsing(basketfi_team):
    """Test parsing team data."""
    assert "team" in basketfi_team
    team = basketfi_team["team"]

    # Verify basic team structure
    assert "team_id" in team
    assert "team_name" in team
    assert "club_name" in team

    # Verify players exist
    assert "players" in team
    assert len(team["players"]) > 0


def test_basketfi_parser_extract_teams():
    """Test extracting unique teams from matches."""
    # Create mock matches data (using the format expected by extract_teams_from_matches)
    matches = [
        {
            "home_team_id": "1",
            "home_team": "Team A",
            "away_team_id": "2",
            "away_team": "Team B",
        },
        {
            "home_team_id": "1",
            "home_team": "Team A",
            "away_team_id": "3",
            "away_team": "Team C",
        },
    ]

    teams = BasketFiParser.extract_teams_from_matches(matches)

    # Should have 3 unique teams
    assert len(teams) == 3
    team_ids = {t["team_id"] for t in teams}
    assert team_ids == {"1", "2", "3"}


# =============================================================================
# MOCKED API TESTS - Test API logic without making real calls
# =============================================================================


def test_basketfi_get_matches_mocked(basketfi_matches):
    """Test get_matches API method with mocked response."""
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = basketfi_matches
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        data = BasketFiAPI.get_matches(competition_id="huki2526", category_id="4")

        assert "matches" in data
        assert len(data["matches"]) > 0
        assert data["_status_code"] == 200


def test_genius_boxscore_mocked(genius_boxscore_html):
    """Test get_match_boxscore API method with mocked response."""
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.text = genius_boxscore_html
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        boxscore = GeniusSportsAPI.get_match_boxscore("123456")

        # Verify structure
        assert "match_info" in boxscore
        assert "teams" in boxscore
        assert len(boxscore["teams"]) == 2


def test_genius_team_statistics_mocked(genius_team_statistics_html):
    """Test get_team_statistics API method with mocked response."""
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.text = genius_team_statistics_html
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        stats = GeniusSportsAPI.get_team_statistics("42145", "40154")

        # Verify structure
        assert "competition_id" in stats
        assert "team_id" in stats
        assert "team_name" in stats
        assert "team_location" in stats
        assert "averages" in stats
        assert "shooting" in stats
        assert "totals" in stats

        # Verify data
        assert stats["competition_id"] == "42145"
        assert stats["team_id"] == "40154"
        assert stats["team_name"] == "ACO Basket"
        assert len(stats["averages"]) == 12
        assert len(stats["shooting"]) == 12
        assert len(stats["totals"]) == 12


# =============================================================================
# DATA VALIDATION TESTS - Test data quality and consistency
# =============================================================================


def test_match_status_consistency(basketfi_matches):
    """Test that match status is consistent with score availability."""
    matches = basketfi_matches.get("matches", [])

    played_count = 0
    fixture_count = 0

    for match in matches:
        status = match.get("status")

        if status == "Played":
            played_count += 1
            # Played matches should have scores
            assert match.get("fs_A") is not None, "Played match should have home score"
            assert match.get("fs_B") is not None, "Played match should have away score"
        elif status == "Fixture":
            fixture_count += 1

    assert played_count > 0, "Should have some played matches"
    print(f"\nMatch status: {played_count} played, {fixture_count} fixtures")


def test_boxscore_has_all_stats(genius_boxscore):
    """Test that boxscore has all expected player statistics."""
    assert "teams" in genius_boxscore
    assert len(genius_boxscore["teams"]) == 2

    for team in genius_boxscore["teams"]:
        assert "team_name" in team
        assert "players" in team
        assert "totals" in team
        assert "coaches" in team

        if team["players"]:
            # Verify player stats
            player = team["players"][0]
            required_stats = ["Player", "Points", "Total Rebounds", "Assists"]
            for stat in required_stats:
                assert stat in player, f"Player should have {stat}"


def test_team_has_roster(basketfi_team):
    """Test that team data includes complete roster information."""
    team = basketfi_team["team"]

    # Check players
    assert "players" in team
    assert len(team["players"]) > 0

    # Check player structure
    player = team["players"][0]
    assert "player_id" in player
    assert "first_name" in player or "last_name" in player

    # Check officials (coaching staff)
    if "officials" in team:
        assert len(team["officials"]) > 0


def test_category_has_seasons(basketfi_category):
    """Test that category data includes season information."""
    assert "category" in basketfi_category
    category = basketfi_category["category"]

    assert "category_id" in category
    assert "category_name" in category
    assert "seasons" in category
    assert len(category["seasons"]) > 0

    # Check season structure
    season = category["seasons"][0]
    assert "season_id" in season
    assert "season_name" in season
    assert "competition_id" in season
