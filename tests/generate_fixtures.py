"""Generate test fixtures by calling the actual APIs."""

import json
import sys
from pathlib import Path

# Add parent directory to path to import the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from koris_api.basketfi_api import BasketFiAPI
from koris_api.genius_api import GeniusSportsAPI
from koris_api.genius_parser import GeniusSportsParser


def generate_basketfi_fixtures():
    """Generate fixtures from Basket.fi API."""
    print("Generating Basket.fi fixtures...")
    fixtures_dir = Path(__file__).parent / "fixtures" / "basketfi"

    # 1. Get a small set of matches from current season
    print("  Fetching matches...")
    matches_data = BasketFiAPI.get_matches(competition_id="huki2526", category_id="4")

    # Save full matches response
    with open(fixtures_dir / "matches.json", "w", encoding="utf-8") as f:
        json.dump(matches_data, f, indent=2, ensure_ascii=False)
    print(f"    Saved {len(matches_data.get('matches', []))} matches")

    # 2. Get detailed match data for one played match
    played_matches = [
        m
        for m in matches_data.get("matches", [])
        if m.get("status") == "Played" and m.get("match_external_id")
    ]

    if played_matches:
        match_id = played_matches[0]["match_id"]
        print(f"  Fetching match details for match {match_id}...")
        match_data = BasketFiAPI.get_match(str(match_id))

        with open(fixtures_dir / "match_detail.json", "w", encoding="utf-8") as f:
            json.dump(match_data, f, indent=2, ensure_ascii=False)
        print(f"    Saved match detail")

    # 3. Get team data
    if matches_data.get("matches"):
        team_id = matches_data["matches"][0].get("team_A_id")
        if team_id:
            print(f"  Fetching team data for team {team_id}...")
            team_data = BasketFiAPI.get_team(str(team_id))

            with open(fixtures_dir / "team.json", "w", encoding="utf-8") as f:
                json.dump(team_data, f, indent=2, ensure_ascii=False)
            print(f"    Saved team data")

    # 4. Get category data
    print("  Fetching category data...")
    category_data = BasketFiAPI.get_category("huki2526", "4")

    with open(fixtures_dir / "category.json", "w", encoding="utf-8") as f:
        json.dump(category_data, f, indent=2, ensure_ascii=False)
    print(f"    Saved category data")

    print("✓ Basket.fi fixtures generated\n")
    return played_matches[0] if played_matches else None


def generate_genius_sports_fixtures(played_match=None):
    """Generate fixtures from Genius Sports API."""
    print("Generating Genius Sports fixtures...")
    fixtures_dir = Path(__file__).parent / "fixtures" / "genius_sports"

    # Use the match external ID from basket.fi if available
    if played_match and played_match.get("match_external_id"):
        match_external_id = str(played_match["match_external_id"])
        print(f"  Fetching box score for match {match_external_id}...")

        try:
            boxscore_data = GeniusSportsAPI.get_match_boxscore(match_external_id)

            with open(fixtures_dir / "boxscore.json", "w", encoding="utf-8") as f:
                json.dump(boxscore_data, f, indent=2, ensure_ascii=False)
            print(f"    Saved box score")
        except Exception as e:
            print(f"    Error fetching box score: {e}")

    # Get a minimal set of teams for a competition
    # Use a known competition ID (e.g., 69 for Korisliiga 2024-25)
    competition_id = "69"
    print(f"  Fetching teams for competition {competition_id}...")

    try:
        teams = GeniusSportsAPI.get_genius_teams(competition_id)

        with open(fixtures_dir / "teams.json", "w", encoding="utf-8") as f:
            json.dump(teams, f, indent=2, ensure_ascii=False)
        print(f"    Saved {len(teams)} teams")

        # Get player data for just one player from the first team
        if teams:
            team_id = teams[0]["id"]
            print(f"  Fetching roster for first team (ID: {team_id})...")

            # This will be very slow, so we'll just fetch minimal data
            # We'll create a mock response instead
            print("    Skipping full player fetch (too slow for fixture generation)")
            print("    Using existing example_responses instead")

    except Exception as e:
        print(f"    Error fetching teams: {e}")

    print("✓ Genius Sports fixtures generated\n")


def main():
    """Generate all test fixtures."""
    print("=" * 60)
    print("GENERATING TEST FIXTURES")
    print("=" * 60)
    print()

    # Generate fixtures
    played_match = generate_basketfi_fixtures()
    generate_genius_sports_fixtures(played_match)

    print("=" * 60)
    print("ALL FIXTURES GENERATED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    main()
