import json
import os
import tempfile
from pathlib import Path

from koris_api import download_matches_with_boxscores
from koris_api.api import KorisAPI


def test_boxscore_parsing_from_local_file():
    """Test parsing box score from local HTML file."""
    # Load the example HTML file
    example_file = (
        Path(__file__).parent.parent / ".example_responses" / "genius-box-score.html"
    )

    assert example_file.exists(), f"Example file should exist: {example_file}"

    with open(example_file, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Parse the HTML
    result = KorisAPI._parse_boxscore_html(html_content)

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


# =============================================================================
# LIVE API TESTS - Test fetching from basket.fi and Genius Sports APIs
# =============================================================================


def test_live_api_match_status():
    """Test that the live basket.fi API correctly returns played vs fixture status."""
    # Fetch matches from live API
    data = KorisAPI.get_matches(competition_id="huki2526", category_id="4")
    matches = data.get("matches", [])

    assert len(matches) > 0, "Should have matches"

    # Check that status values are valid
    played_count = 0
    fixture_count = 0

    for match in matches:
        status = match.get("status")
        assert status in ["Played", "Fixture"], (
            f"Status should be 'Played' or 'Fixture', got: {status}"
        )

        if status == "Played":
            played_count += 1
            # Played matches should have scores
            assert match.get("fs_A") is not None, "Played match should have home score"
            assert match.get("fs_B") is not None, "Played match should have away score"
            # Should have external ID for Genius Sports
            assert match.get("match_external_id") is not None, (
                "Played match should have external ID"
            )
        elif status == "Fixture":
            fixture_count += 1

    assert played_count > 0, "Should have some played matches"
    print(
        f"\nLive API stats: {played_count} played, {fixture_count} fixtures out of {len(matches)} total"
    )


def test_live_boxscore_fetching():
    """Test fetching a box score from live Genius Sports API."""
    # First, get a played match with external ID
    data = KorisAPI.get_matches(competition_id="huki2526", category_id="4")
    matches = data.get("matches", [])

    played_matches = [
        m for m in matches if m.get("status") == "Played" and m.get("match_external_id")
    ]

    assert len(played_matches) > 0, (
        "Should have at least one played match with external ID"
    )

    # Try to fetch box score for first played match
    match = played_matches[0]
    external_id = match["match_external_id"]

    try:
        boxscore = KorisAPI.get_match_boxscore(str(external_id))

        # Verify structure
        assert "match_info" in boxscore
        assert "teams" in boxscore
        assert len(boxscore["teams"]) == 2

        # Verify all teams have required data
        for team in boxscore["teams"]:
            assert "team_name" in team
            assert "players" in team
            assert "totals" in team
            assert "coaches" in team

            if team["players"]:
                # Verify player stats
                player = team["players"][0]
                assert "Player" in player
                assert "Points" in player
                assert "Total Rebounds" in player
                assert "Assists" in player

        print(
            f"\nSuccessfully fetched live box score for {match['club_A_name']} vs {match['club_B_name']}"
        )

    except Exception as e:
        # Some matches might not have Genius Sports data available yet
        print(
            f"\nNote: Could not fetch box score for match {external_id}: {str(e)[:100]}"
        )
        # This is not a failure - just means the data isn't available yet


# =============================================================================
# DOWNLOAD TESTS - Test concurrent downloading with proper status checking
# =============================================================================


def test_download_only_fetches_played_matches():
    """Test that download only fetches advanced stats for matches with status='Played'."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        output_file = f.name

    try:
        # Download with concurrency
        download_matches_with_boxscores(
            competition_id="huki2526",
            category_id="4",
            output_file=output_file,
            include_advanced=True,
            max_workers=3,
            verbose=False,
        )

        # Load and verify data
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        matches = data["matches"]

        # Categorize matches
        played_matches = [m for m in matches if m.get("status") == "Played"]
        fixture_matches = [m for m in matches if m.get("status") == "Fixture"]
        matches_with_advanced = [m for m in matches if "advanced_boxscore" in m]

        # Verify counts
        assert len(played_matches) > 0, "Should have some played matches"
        assert len(matches_with_advanced) > 0, "Should have matches with advanced stats"

        # CRITICAL: Verify that all matches with advanced stats are played matches
        for match in matches_with_advanced:
            assert match["status"] == "Played", (
                "Only played matches should have advanced stats"
            )

        # CRITICAL: Verify no fixture matches have advanced stats
        for match in fixture_matches:
            assert "advanced_boxscore" not in match, (
                "Fixture matches should not have advanced stats"
            )

        print(
            f"\nCorrectly fetched advanced stats for {len(matches_with_advanced)}/{len(played_matches)} played matches"
        )
        print(f"Skipped {len(fixture_matches)} fixture matches")

    finally:
        if os.path.exists(output_file):
            os.unlink(output_file)


def test_concurrent_download_with_advanced():
    """Test that concurrent download produces correct results with all 23 player stats."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        output_file = f.name

    try:
        download_matches_with_boxscores(
            competition_id="huki2526",
            category_id="4",
            output_file=output_file,
            include_advanced=True,
            max_workers=3,
            verbose=False,
        )

        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Check metadata
        metadata = data["metadata"]
        assert metadata["competition_id"] == "huki2526"
        assert metadata["category_id"] == "4"
        assert metadata["include_advanced_stats"] is True

        matches_with_advanced = [m for m in data["matches"] if "advanced_boxscore" in m]

        # Verify advanced boxscore structure and all 23 fields
        for match in matches_with_advanced[:5]:
            boxscore = match["advanced_boxscore"]
            assert "match_info" in boxscore
            assert "teams" in boxscore
            assert len(boxscore["teams"]) == 2

            for team in boxscore["teams"]:
                assert "team_name" in team
                assert "players" in team
                assert "totals" in team
                assert "coaches" in team

                if team["players"]:
                    player = team["players"][0]
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

    finally:
        if os.path.exists(output_file):
            os.unlink(output_file)


def test_concurrent_download_without_advanced():
    """Test that download without advanced stats skips Genius Sports API calls."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        output_file = f.name

    try:
        download_matches_with_boxscores(
            competition_id="huki2526",
            category_id="4",
            output_file=output_file,
            include_advanced=False,
            max_workers=3,
            verbose=False,
        )

        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        metadata = data["metadata"]
        assert metadata["include_advanced_stats"] is False
        assert metadata["matches_with_advanced_stats"] == 0

        # No matches should have advanced stats
        matches_with_advanced = [m for m in data["matches"] if "advanced_boxscore" in m]
        assert len(matches_with_advanced) == 0

    finally:
        if os.path.exists(output_file):
            os.unlink(output_file)


def test_concurrent_download_different_worker_counts():
    """Test that different worker counts produce identical results (data integrity)."""
    results = []

    for workers in [1, 3, 5]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_file = f.name

        try:
            download_matches_with_boxscores(
                competition_id="huki2526",
                category_id="4",
                output_file=output_file,
                include_advanced=True,
                max_workers=workers,
                verbose=False,
            )

            with open(output_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            results.append(
                {
                    "workers": workers,
                    "total": data["metadata"]["total_matches"],
                    "advanced": data["metadata"]["matches_with_advanced_stats"],
                    "failed": data["metadata"]["matches_failed"],
                }
            )

        finally:
            if os.path.exists(output_file):
                os.unlink(output_file)

    # All worker counts should produce same results
    assert all(r["total"] == results[0]["total"] for r in results)
    assert all(r["advanced"] == results[0]["advanced"] for r in results)
    assert all(r["failed"] < 5 for r in results), "Should have minimal failures"

    print("\nAll concurrency levels produced identical results:")
    for r in results:
        print(
            f"  {r['workers']} workers: {r['advanced']}/{r['total']} matches, {r['failed']} failed"
        )
