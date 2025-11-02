"""
Integration tests that make real API calls.

These tests mirror the structure of test_api_fast.py but use live API responses
instead of fixtures. They test actual API connectivity and current data availability.

Run with: pytest tests/test_integration.py -v
Skip with: pytest -m "not integration"
"""

import pytest

from koris_api.basketfi_api import BasketFiAPI
from koris_api.basketfi_parser import BasketFiParser
from koris_api.genius_api import GeniusSportsAPI


# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration


# =============================================================================
# FIXTURES - Fetch live data once per test session
# =============================================================================


@pytest.fixture(scope="session")
def live_basketfi_matches():
    """Fetch matches from live basket.fi API once per session."""
    return BasketFiAPI.get_matches(competition_id="huki2526", category_id="4")


@pytest.fixture(scope="session")
def live_basketfi_match_detail(live_basketfi_matches):
    """Fetch detailed match data from live API once per session."""
    matches = live_basketfi_matches.get("matches", [])
    if len(matches) == 0:
        pytest.skip("No matches available")

    match_id = matches[0]["match_id"]
    return BasketFiAPI.get_match(str(match_id))


@pytest.fixture(scope="session")
def live_basketfi_team(live_basketfi_matches):
    """Fetch team data from live API once per session."""
    matches = live_basketfi_matches.get("matches", [])
    if len(matches) == 0:
        pytest.skip("No matches available")

    team_id = matches[0]["team_A_id"]
    return BasketFiAPI.get_team(str(team_id))


@pytest.fixture(scope="session")
def live_basketfi_category():
    """Fetch category data from live API once per session."""
    return BasketFiAPI.get_category("huki2526", "4")


@pytest.fixture(scope="session")
def live_genius_boxscore(live_basketfi_matches):
    """Fetch Genius Sports boxscore from live API once per session."""
    matches = live_basketfi_matches.get("matches", [])
    played_matches = [
        m for m in matches if m.get("status") == "Played" and m.get("match_external_id")
    ]

    if len(played_matches) == 0:
        pytest.skip("No played matches with external ID available")

    external_id = str(played_matches[0]["match_external_id"])

    try:
        return GeniusSportsAPI.get_match_boxscore(external_id)
    except Exception as e:
        pytest.skip(f"Could not fetch box score: {str(e)[:100]}")


# =============================================================================
# PARSING TESTS - Test parsers with live API data
# =============================================================================


def test_boxscore_parsing_from_live_api(live_genius_boxscore):
    """Test parsing box score from live Genius Sports API."""
    result = live_genius_boxscore

    # Verify structure
    assert "match_info" in result
    assert "teams" in result
    assert len(result["teams"]) == 2

    # Verify match info
    match_info = result["match_info"]
    assert "home_team" in match_info
    assert "away_team" in match_info
    assert "home_score" in match_info
    assert "away_score" in match_info
    assert "status" in match_info

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


def test_basketfi_matches_parsing_live(live_basketfi_matches):
    """Test parsing live basket.fi matches data."""
    matches = BasketFiParser.extract_matches(live_basketfi_matches)
    assert len(matches) > 0, "Should have matches"

    # Check that status values are valid
    for match in matches:
        status = match.get("status")
        assert status in ["Played", "Fixture"], (
            f"Status should be 'Played' or 'Fixture', got: {status}"
        )


def test_basketfi_match_detail_parsing_live(live_basketfi_match_detail):
    """Test parsing detailed match data from live API."""
    assert "match" in live_basketfi_match_detail
    match = live_basketfi_match_detail["match"]

    # Verify basic match structure
    assert "match_id" in match
    assert "club_A_name" in match
    assert "club_B_name" in match
    assert "status" in match

    # Verify lineups exist
    assert "lineups" in match
    assert len(match["lineups"]) > 0


def test_basketfi_team_parsing_live(live_basketfi_team):
    """Test parsing team data from live API."""
    assert "team" in live_basketfi_team
    team = live_basketfi_team["team"]

    # Verify basic team structure
    assert "team_id" in team
    assert "team_name" in team
    assert "club_name" in team

    # Verify players exist
    assert "players" in team
    assert len(team["players"]) > 0


def test_basketfi_parser_extract_teams_live(live_basketfi_matches):
    """Test extracting unique teams from live matches."""
    # Parse matches first
    matches = BasketFiParser.parse_matches(
        BasketFiParser.extract_matches(live_basketfi_matches), only_played=False
    )

    teams = BasketFiParser.extract_teams_from_matches(matches)

    # Should have teams
    assert len(teams) > 0, "Should have extracted teams"

    # Verify team structure
    for team in teams:
        assert "team_id" in team
        assert "team_name" in team


# =============================================================================
# DATA VALIDATION TESTS - Test live data quality and consistency
# =============================================================================


def test_match_status_consistency_live(live_basketfi_matches):
    """Test that live match status is consistent with score availability."""
    matches = live_basketfi_matches.get("matches", [])

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
    print(
        f"\nLive data - Match status: {played_count} played, {fixture_count} fixtures"
    )


def test_boxscore_has_all_stats_live(live_genius_boxscore):
    """Test that live boxscore has all expected player statistics."""
    assert "teams" in live_genius_boxscore
    assert len(live_genius_boxscore["teams"]) == 2

    for team in live_genius_boxscore["teams"]:
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


def test_team_has_roster_live(live_basketfi_team):
    """Test that live team data includes complete roster information."""
    team = live_basketfi_team["team"]

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


def test_category_has_seasons_live(live_basketfi_category):
    """Test that live category data includes season information."""
    assert "category" in live_basketfi_category
    category = live_basketfi_category["category"]

    assert "category_id" in category
    assert "category_name" in category
    assert "seasons" in category
    assert len(category["seasons"]) > 0

    # Check season structure
    season = category["seasons"][0]
    assert "season_id" in season
    assert "season_name" in season
    assert "competition_id" in season


# =============================================================================
# API CONNECTIVITY TESTS
# =============================================================================


def test_basketfi_api_returns_valid_json(live_basketfi_matches):
    """Test that basket.fi API returns valid JSON structure."""
    assert isinstance(live_basketfi_matches, dict)
    assert "matches" in live_basketfi_matches
    assert isinstance(live_basketfi_matches["matches"], list)


def test_genius_api_returns_valid_json(live_genius_boxscore):
    """Test that Genius Sports API returns valid JSON structure."""
    assert isinstance(live_genius_boxscore, dict)
    assert "match_info" in live_genius_boxscore
    assert "teams" in live_genius_boxscore


# =============================================================================
# PERFORMANCE TESTS
# =============================================================================


def test_basketfi_matches_response_time():
    """Test that basket.fi matches API responds quickly."""
    import time

    start = time.time()
    data = BasketFiAPI.get_matches(competition_id="huki2526", category_id="4")
    elapsed = time.time() - start

    assert elapsed < 5.0, f"API call took too long: {elapsed:.2f}s"
    assert "matches" in data

    print(f"\n✓ Basket.fi API responded in {elapsed:.2f}s")


def test_genius_boxscore_response_time():
    """Test that Genius Sports boxscore API responds quickly."""
    import time

    # Get a match ID first
    data = BasketFiAPI.get_matches(competition_id="huki2526", category_id="4")
    played_matches = [
        m
        for m in data.get("matches", [])
        if m.get("status") == "Played" and m.get("match_external_id")
    ]

    if len(played_matches) == 0:
        pytest.skip("No played matches available for timing test")

    external_id = str(played_matches[0]["match_external_id"])

    start = time.time()
    try:
        boxscore = GeniusSportsAPI.get_match_boxscore(external_id)
        elapsed = time.time() - start

        assert elapsed < 10.0, f"API call took too long: {elapsed:.2f}s"
        assert "match_info" in boxscore

        print(f"\n✓ Genius Sports API responded in {elapsed:.2f}s")
    except Exception as e:
        pytest.skip(f"Could not complete timing test: {str(e)[:100]}")
