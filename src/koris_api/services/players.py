from ..genius_api import GeniusSportsAPI


def download_players_season(
    competition_id: str,
    output_file: str,
    verbose: bool = True,
) -> None:
    """Download all players and their gamelogs for a specific Genius Sports competition."""

    if verbose:
        print(f"Fetching players for Genius Sports competition {competition_id}...")
        print(f"{'=' * 60}\n")

    try:
        # Use the API method to get all players
        result = GeniusSportsAPI.get_genius_players(
            competition_id=competition_id, output_file=output_file
        )

        # Summary already printed by get_genius_players
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"✓ Successfully saved player data to {output_file}")
            print(f"  - Competition ID: {competition_id}")
            print(f"  - Teams: {len(result['teams'])}")
            print(f"  - Players: {len(result['players'])}")

            # Count players with errors
            errors = sum(1 for p in result["players"] if "error" in p)
            if errors > 0:
                print(f"  - Players with errors: {errors}")

            # Count total games
            total_games = sum(len(p.get("games", [])) for p in result["players"])
            print(f"  - Total games logged: {total_games}")
            print(f"{'=' * 60}")

    except Exception as e:
        print(f"Error downloading players: {str(e)}")
        raise


def download_players_by_team(
    competition_id: str,
    team_id: str,
    output_file: str,
    verbose: bool = True,
) -> None:
    """Download players and their gamelogs for a specific team in a Genius Sports competition."""

    if verbose:
        print(f"Fetching players for team {team_id} in competition {competition_id}...")
        print(f"{'=' * 60}\n")

    try:
        # Use the API method to get players by team
        result = GeniusSportsAPI.get_genius_players_by_team(
            competition_id=competition_id, team_id=team_id, output_file=output_file
        )

        # Summary already printed by get_genius_players_by_team
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"✓ Successfully saved player data to {output_file}")
            print(f"  - Competition ID: {competition_id}")
            print(f"  - Team: {result.get('team_name', 'Unknown')} (ID: {team_id})")
            print(f"  - Players: {len(result['players'])}")

            # Count total games
            total_games = sum(len(p.get("games", [])) for p in result["players"])
            print(f"  - Total games logged: {total_games}")
            print(f"{'=' * 60}")

    except Exception as e:
        print(f"Error downloading players: {str(e)}")
        raise
