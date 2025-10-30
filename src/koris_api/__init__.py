import argparse
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any
from tqdm import tqdm
from .api import KorisAPI

__version__ = "0.1.0"
__all__ = ["KorisAPI", "download_matches_with_boxscores", "main"]


def download_matches_with_boxscores(
    competition_id: str,
    category_id: str,
    output_file: str,
    include_advanced: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """Download all matches for a season, optionally including advanced box scores."""

    if verbose:
        print(
            f"Fetching matches for competition {competition_id}, category {category_id}..."
        )

    # Fetch all matches
    data = KorisAPI.get_matches(competition_id=competition_id, category_id=category_id)
    matches = data.get("matches", [])

    if not matches:
        print("No matches found.")
        return

    total_matches = len(matches)
    if verbose:
        print(f"Found {total_matches} matches")

    # Process basic match data first
    processed_matches = []
    matches_to_fetch_advanced = []

    for match in matches:
        # Check if match has been played (has scores)
        home_score = match.get("fs_A")
        away_score = match.get("fs_B")
        is_played = (
            home_score is not None
            and away_score is not None
            and home_score != ""
            and away_score != ""
        )

        # Only process played matches
        if not is_played:
            continue

        match_data = {
            "match_id": match.get("match_id"),
            "match_external_id": match.get("match_external_id"),
            "date": match.get("date"),
            "time": match.get("time"),
            "home_team": match.get("club_A_name"),
            "home_team_id": match.get("team_A_id"),
            "away_team": match.get("club_B_name"),
            "away_team_id": match.get("team_B_id"),
            "home_score": home_score,
            "away_score": away_score,
            "status": match.get("status"),
            "venue": match.get("venue_name"),
            "competition": match.get("competition_name"),
            "category": match.get("category_name"),
            "season": match.get("season_id"),
        }

        processed_matches.append(match_data)

        # Check if we should fetch advanced stats for this match
        if include_advanced:
            external_id = match.get("match_external_id")
            if external_id:
                matches_to_fetch_advanced.append(
                    {
                        "index": len(processed_matches) - 1,
                        "external_id": external_id,
                        "home_team": match_data["home_team"],
                        "away_team": match_data["away_team"],
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
        ) -> tuple[int, Optional[Dict[str, Any]], Optional[str]]:
            """Fetch box score for a single match. Returns (index, boxscore_data, error_msg)."""
            try:
                boxscore = KorisAPI.get_match_boxscore(str(match_info["external_id"]))
                return (match_info["index"], boxscore, None)
            except Exception as e:
                return (match_info["index"], None, str(e))

        # Use ThreadPoolExecutor for concurrent fetching
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(fetch_boxscore, match_info): match_info
                for match_info in matches_to_fetch_advanced
            }

            # Process results with progress bar
            with tqdm(
                total=len(matches_to_fetch_advanced),
                desc="Fetching advanced stats",
                disable=not verbose,
            ) as pbar:
                for future in as_completed(futures):
                    match_info = futures[future]
                    index, boxscore, error = future.result()

                    if boxscore:
                        processed_matches[index]["advanced_boxscore"] = boxscore
                        matches_with_advanced += 1
                    else:
                        matches_failed += 1
                        if verbose and error:
                            tqdm.write(
                                f"  ✗ Failed {match_info['home_team']} vs {match_info['away_team']}: {error[:50]}"
                            )

                    pbar.update(1)

    # Save to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "metadata": {
            "competition_id": competition_id,
            "category_id": category_id,
            "total_matches_in_season": total_matches,
            "played_matches_saved": len(processed_matches),
            "matches_with_advanced_stats": matches_with_advanced,
            "matches_failed": matches_failed,
            "include_advanced_stats": include_advanced,
        },
        "matches": processed_matches,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(
            f"Successfully saved {len(processed_matches)} played matches to {output_path}"
        )
        print(f"  - Total matches in season: {total_matches}")
        print(f"  - Played matches saved: {len(processed_matches)}")
        if include_advanced:
            print(
                f"  - Advanced stats: {matches_with_advanced}/{len(matches_to_fetch_advanced)} matches"
            )
            if matches_failed > 0:
                print(f"  - Failed: {matches_failed}")
        print(f"{'=' * 60}")


def main() -> None:
    """CLI entry point for koris-api."""
    epilog = """
examples:
  # Download entire season with advanced statistics
  koris-api --action download-season --output season.json --category-id 4 --advanced
  
  # Get all matches for Korisliiga
  koris-api --action matches --competition-id huki2526 --category-id 4
  
  # Get team information
  koris-api --action team --team-id 12345 --output team.json
  
  # Get match details
  koris-api --action match --match-id 2701885 --output match.json
  
  # Get category info with available seasons
  koris-api --action category --competition-id huki2526 --category-id 4

common category IDs:
  4  - Korisliiga (Men's top division)
  2  - Miesten I divisioona A (Men's 1st division A)
  13 - Naisten Korisliiga (Women's top division)
"""

    parser = argparse.ArgumentParser(
        description="Access Koris API from command line",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--action",
        choices=["matches", "team", "match", "category", "download-season"],
        required=True,
        metavar="ACTION",
        help="Action to perform: matches (get all matches), team (get team info), "
        "match (get match details), category (get category/season info), "
        "download-season (download full season with optional advanced stats)",
    )
    parser.add_argument(
        "--competition-id",
        default="huki2526",
        metavar="ID",
        help="Competition ID (default: huki2526 for current season)",
    )
    parser.add_argument(
        "--category-id",
        default="4",
        metavar="ID",
        help="Category ID (default: 4 for Korisliiga)",
    )
    parser.add_argument(
        "--team-id", metavar="ID", help="Team ID (required for --action team)"
    )
    parser.add_argument(
        "--match-id", metavar="ID", help="Match ID (required for --action match)"
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Output file path (prints to stdout if not specified, required for download-season)",
    )
    parser.add_argument(
        "--advanced",
        action="store_true",
        help="Include Genius Sports advanced box scores (only for download-season action)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        metavar="N",
        help="Number of concurrent workers for fetching advanced stats (default: 5)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output and status messages",
    )

    args = parser.parse_args()

    try:
        data = None

        if args.action == "download-season":
            if not args.output:
                parser.error("--output is required for download-season action")
            download_matches_with_boxscores(
                competition_id=args.competition_id,
                category_id=args.category_id,
                output_file=args.output,
                include_advanced=args.advanced,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )
            return  # Exit after download
        elif args.action == "matches":
            data = KorisAPI.get_matches(
                competition_id=args.competition_id, category_id=args.category_id
            )
        elif args.action == "team":
            if not args.team_id:
                parser.error("--team-id is required for team action")
            data = KorisAPI.get_team(args.team_id)
        elif args.action == "match":
            if not args.match_id:
                parser.error("--match-id is required for match action")
            data = KorisAPI.get_match(args.match_id)
        elif args.action == "category":
            data = KorisAPI.get_category(args.competition_id, args.category_id)
        else:
            parser.error(f"Unknown action: {args.action}")
            return

        # Output the data (for non-download actions)
        if data is not None:
            json_str = json.dumps(data, indent=2, ensure_ascii=False)

            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json_str, encoding="utf-8")
                print(f"Data saved to {output_path}")
            else:
                print(json_str)

    except Exception as e:
        print(f"Error: {e}")
