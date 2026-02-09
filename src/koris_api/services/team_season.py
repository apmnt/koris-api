import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any

import requests
from tqdm import tqdm

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..boxscore_normalizer import normalize_boxscore
from ..genius_api import GeniusSportsAPI
from .common import _get_genius_session, resolve_genius_competition_id


def download_team_season(
    team_id: str,
    category_id: Optional[str],
    competition_id: str,
    output_file: str,
    season_name: Optional[str] = None,
    include_advanced: bool = False,
    include_team_stats: bool = False,
    genius_competition_id: Optional[str] = None,
    genius_team_id: Optional[str] = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """
    Download all matches of one team from one season.

    Optionally includes player data from advanced boxscores (if --adv-players flag is used).
    Optionally includes team season statistics (if --adv-teams flag is used).
    Player data comes from match boxscores, not separate player downloads.

    This fetches:
    - All matches for the team in the season (played matches only)
    - Team details with roster and staff
    - Advanced box scores with player stats per match (optional)
    - Team season statistics - averages, shooting, totals (optional)

    All data is saved to a single structured JSON file.

    Args:
        team_id: The team identifier
        category_id: Optional category/league identifier (e.g., "4" for Korisliiga). If not provided, will be auto-detected from team's matches.
        competition_id: The competition/season identifier (e.g., "huki2526")
        output_file: Path where output file will be saved
        season_name: Optional season name (e.g., "2024-2025") for metadata
        include_advanced: Whether to include advanced box scores with player data from Genius Sports
        include_team_stats: Whether to include team season statistics from Genius Sports
        genius_competition_id: Genius Sports competition ID (required for team stats)
        genius_team_id: Genius Sports team ID (required for team stats)
        max_workers: Number of concurrent workers for parallel downloads
        verbose: Whether to show progress output
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get team info first
    if verbose:
        print(f"\n{'=' * 80}")
        print("TEAM SEASON DATA DOWNLOAD")
        print(f"{'=' * 80}")
        print(f"Team ID: {team_id}")

    team_data = BasketFiAPI.get_team(str(team_id))
    team_name = "Unknown"
    if "team" in team_data:
        team_name = BasketFiParser.extract_team_name(team_data)

    # Auto-detect category_id if not provided
    if not category_id:
        if verbose:
            print(f"Category ID: Auto-detecting from team matches...")

        # Fetch team matches to determine category
        matches_data = BasketFiAPI.get_matches(team_id=team_id)
        all_matches = BasketFiParser.extract_matches(matches_data)

        # Find a match with the requested competition_id to get category_id
        for match in all_matches:
            if match.get("competition_id") == competition_id:
                category_id = match.get("category_id")
                if category_id:
                    if verbose:
                        print(f"Category ID: {category_id} (auto-detected)")
                    break

        if not category_id:
            # Fallback: use the first match's category_id if available
            if all_matches and all_matches[0].get("category_id"):
                category_id = all_matches[0].get("category_id")
                if verbose:
                    print(
                        f"Category ID: {category_id} (auto-detected from first match)"
                    )
            else:
                if verbose:
                    print("Warning: Could not auto-detect category_id")
                category_id = "Unknown"
    else:
        if verbose:
            print(f"Category ID: {category_id}")

    if verbose:
        print(f"Competition ID: {competition_id}")
        if season_name:
            print(f"Season: {season_name}")
        print(f"Output file: {output_path.absolute()}")
        print(f"Include advanced stats (with player data): {include_advanced}")
        print(f"{'=' * 80}\n")

    # Get category info
    if verbose:
        print("Fetching league information...")

    category_name = "Unknown"
    if category_id and category_id != "Unknown":
        try:
            category_data = BasketFiAPI.get_category(competition_id, category_id)
            if "category" in category_data:
                category_name = category_data["category"].get(
                    "category_name", "Unknown"
                )
        except Exception:
            pass

    if verbose:
        print(f"✓ League: {category_name}")
        print(f"✓ Team: {team_name}\n")

    # Initialize data structure
    result_data = {
        "metadata": {
            "team_id": team_id,
            "team_name": team_name,
            "category_id": category_id,
            "category_name": category_name,
            "competition_id": competition_id,
            "season_name": season_name or competition_id,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "include_advanced_stats": include_advanced,
        },
        "team": team_data.get("team", {}),
        "matches": [],
    }

    # Fetch matches for this team
    if verbose:
        print("Downloading all matches for the team...")

    # Fetch matches for this team (API only accepts team_id OR competition_id+category_id, not both)
    matches_data = BasketFiAPI.get_matches(team_id=team_id)
    all_matches = BasketFiParser.extract_matches(matches_data)

    # Filter matches by season/competition if needed
    matches = BasketFiParser.filter_matches_by_season(
        all_matches, competition_id, category_id
    )

    if not matches:
        if verbose:
            print("No matches found.")
        result_data["metadata"]["total_matches"] = 0
        result_data["metadata"]["played_matches_saved"] = 0
    else:
        total_matches = len(matches)

        if verbose:
            print(f"Found {total_matches} matches")

        # Process matches
        processed_matches = BasketFiParser.parse_matches(
            matches, season_name=season_name or competition_id, only_played=True
        )
        matches_to_fetch_advanced = []

        # Check if we should fetch advanced stats
        if include_advanced:
            for idx, match_data in enumerate(processed_matches):
                external_id = match_data.get("match_external_id")
                if external_id:
                    competition_id_resolved = resolve_genius_competition_id(
                        category_id=category_id,
                        season_id=competition_id,
                        match_category_external_id=match_data.get(
                            "category_external_id"
                        ),
                    )
                    matches_to_fetch_advanced.append(
                        {
                            "index": idx,
                            "external_id": external_id,
                            "competition_id": competition_id_resolved,
                            "home_team": match_data["home_team"],
                            "away_team": match_data["away_team"],
                            "match_date": match_data.get("date")
                            or match_data.get("match_date")
                            or "Unknown date",
                            "url": GeniusSportsAPI.build_match_boxscore_url(
                                str(external_id),
                                competition_id=competition_id_resolved,
                            ),
                        }
                    )

        # Fetch advanced stats concurrently if requested
        matches_with_advanced = 0
        matches_failed = 0

        if include_advanced and matches_to_fetch_advanced:
            if verbose:
                print(
                    f"\nFetching advanced box scores for {len(matches_to_fetch_advanced)} played matches..."
                )

            def fetch_boxscore(
                match_info: Dict[str, Any],
            ) -> tuple[int, Optional[Dict[str, Any]], Optional[str], Optional[str]]:
                """Fetch box score for a single match."""
                try:
                    session = _get_genius_session(max_workers)
                    boxscore = GeniusSportsAPI.get_match_boxscore(
                        str(match_info["external_id"]),
                        competition_id=match_info.get("competition_id"),
                        session=session,
                        log_fn=tqdm.write if verbose else None,
                    )
                    return (match_info["index"], boxscore, None, None)
                except requests.exceptions.HTTPError as e:
                    error_type = (
                        f"HTTP {e.response.status_code}" if e.response else "HTTP Error"
                    )
                    return (match_info["index"], None, str(e), error_type)
                except ValueError as e:
                    return (match_info["index"], None, str(e), "Parse Error")
                except Exception as e:
                    error_type = type(e).__name__
                    return (match_info["index"], None, str(e), error_type)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(fetch_boxscore, match_info): match_info
                    for match_info in matches_to_fetch_advanced
                }

                with tqdm(
                    total=len(matches_to_fetch_advanced),
                    desc="Fetching advanced stats",
                    disable=not verbose,
                ) as pbar:
                    for future in as_completed(futures):
                        match_info = futures[future]
                        index, boxscore, error, error_type = future.result()

                        if boxscore:
                            processed_matches[index]["boxscore"] = normalize_boxscore(
                                boxscore, source="genius"
                            )
                            matches_with_advanced += 1
                        else:
                            matches_failed += 1
                            if error:
                                processed_matches[index]["advanced_boxscore_error"] = {
                                    "error_type": error_type or "Unknown",
                                    "error_message": error,
                                }
                            if verbose and error:
                                # Show abbreviated error for common issues
                                if error_type == "Parse Error":
                                    error_display = "Data parsing failed"
                                elif error_type and error_type.startswith("HTTP"):
                                    error_display = error_type
                                else:
                                    # For other errors, show type and short message
                                    error_display = (
                                        f"{error_type}: {error[:40]}"
                                        if error_type
                                        else error[:50]
                                    )

                                tqdm.write(
                                    f"  ✗ {match_info['match_date']} - {match_info['home_team']} vs {match_info['away_team']}: {error_display}"
                                )

                        pbar.update(1)

        result_data["matches"] = processed_matches
        result_data["metadata"]["total_matches"] = total_matches
        result_data["metadata"]["played_matches_saved"] = len(processed_matches)
        result_data["metadata"]["matches_with_advanced_stats"] = matches_with_advanced
        result_data["metadata"]["matches_failed"] = matches_failed

        if verbose:
            print(f"✓ Downloaded {len(processed_matches)} played matches\n")

    # Fetch team statistics if requested
    if include_team_stats:
        if not genius_competition_id or not genius_team_id:
            if verbose:
                print(
                    "WARNING: --adv-teams requires --genius-competition-id and --genius-team-id"
                )
                print("  Skipping team statistics...\n")
        else:
            if verbose:
                print(f"Fetching team season statistics from Genius Sports...")

            try:
                team_stats = GeniusSportsAPI.get_team_statistics(
                    competition_id=genius_competition_id, team_id=genius_team_id
                )
                result_data["team_statistics"] = team_stats
                result_data["metadata"]["include_team_stats"] = True

                if verbose:
                    print(
                        f"✓ Fetched team statistics with {len(team_stats.get('averages', []))} players\n"
                    )
            except Exception as e:
                if verbose:
                    print(f"✗ Error fetching team statistics: {e}\n")
                result_data["metadata"]["team_stats_error"] = str(e)

    # Save to file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    # Final summary
    if verbose:
        print(f"\n{'=' * 80}")
        print("TEAM SEASON DOWNLOAD COMPLETE!")
        print(f"{'=' * 80}")
        print(f"Team: {team_name}")
        print(f"League: {category_name}")
        print(f"Season: {season_name or competition_id}")
        print(f"Output file: {output_path.absolute()}")
        print(f"\nData summary:")
        print(f"  - Matches: {len(result_data['matches'])} (played matches)")
        if include_advanced:
            matches_with_player_data = sum(
                1 for m in result_data["matches"] if "boxscore" in m
            )
            print(f"  - Matches with player data: {matches_with_player_data}")
        if include_team_stats and "team_statistics" in result_data:
            team_stats = result_data["team_statistics"]
            print(f"  - Team statistics: {len(team_stats.get('averages', []))} players")
        print(f"{'=' * 80}\n")
