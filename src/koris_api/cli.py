import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .basketfi_api import BasketFiAPI
from .boxscore_normalizer import normalize_boxscore
from .genius_api import GeniusSportsAPI, GeniusSportsBoxscoreError
from .services.baskethotel_season_boxscores import (
    download_baskethotel_season_boxscores,
)
from .services.league_boxscores import (
    download_league_boxscores_playbyplay_all_seasons,
)
from .services.league_comprehensive import download_league_comprehensive
from .services.season_boxscores import download_matches_with_boxscores
from .services.season_comprehensive import download_season_comprehensive

CURRENT_SEASON_ID = "huki2526"
DEFAULT_OUTPUT_DIR = "output"


def _parse_season_start_year(season_id: str) -> Optional[int]:
    season_id = season_id.strip()
    if "-" in season_id:
        parts = season_id.split("-", 1)
        if len(parts[0]) == 4 and parts[0].isdigit():
            return int(parts[0])
    if season_id.startswith("huki") and len(season_id) >= 8:
        yy = season_id[4:6]
        if yy.isdigit():
            return 2000 + int(yy)
    return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_output_dir(output_dir: Optional[str]) -> Path:
    base = Path(output_dir or DEFAULT_OUTPUT_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base


def main() -> None:
    """CLI entry point for koris-api."""
    epilog = """
examples:
  uv run koris-api match 2514938 --box-score --output-dir out
  uv run koris-api match 2514938 --playbyplay --output-dir out
  uv run koris-api match 2514938 --shot-chart --output-dir out
  uv run koris-api season 4 huki2526 --box-score --output-dir out
  uv run koris-api season 4 2024-2025 --box-score --playbyplay --shot-chart --output-dir out
  uv run koris-api league 4 --output-dir out
  uv run koris-api league 4 --box-score --playbyplay --shot-chart --output-dir out

notes:
  - If no flags are provided for match, --box-score is assumed.
  - If no flags are provided for season or league, a comprehensive dataset is downloaded.
"""

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--box-score",
        action="store_true",
        help="Include box score data",
    )
    shared.add_argument(
        "--playbyplay",
        action="store_true",
        help="Include play-by-play data",
    )
    shared.add_argument(
        "--shot-chart",
        action="store_true",
        help="Include shot chart data (Genius data.json feed)",
    )
    shared.add_argument(
        "--output-dir",
        help="Directory for output files (default: ./output)",
    )
    shared.add_argument(
        "--limit-games",
        type=int,
        help="Limit to the latest N games when downloading season data",
    )

    parser = argparse.ArgumentParser(
        description="Access Koris API from command line",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    match_parser = subparsers.add_parser("match", parents=[shared])
    match_parser.add_argument("match_id", help="Match ID")

    season_parser = subparsers.add_parser("season", parents=[shared])
    season_parser.add_argument("category_id", help="Category ID")
    season_parser.add_argument("season_id", help="Season ID")

    league_parser = subparsers.add_parser("league", parents=[shared])
    league_parser.add_argument("category_id", help="Category ID")

    args = parser.parse_args()

    try:
        if args.action == "match":
            output_dir = _ensure_output_dir(args.output_dir)
            include_box = args.box_score or not (
                args.playbyplay or args.shot_chart
            )
            if include_box:
                output_file = output_dir / f"genius_match_{args.match_id}.json"
                boxscore = GeniusSportsAPI.get_match_boxscore(
                    str(args.match_id),
                    competition_id="12345",
                    not_found_retries=0,
                )
                normalized = normalize_boxscore(boxscore, source="genius")
                output_file.write_text(
                    json.dumps(normalized, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            if args.playbyplay:
                output_file = output_dir / f"genius_playbyplay_{args.match_id}.json"
                playbyplay = GeniusSportsAPI.get_match_playbyplay(str(args.match_id))
                output_file.write_text(
                    json.dumps(playbyplay, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            if args.shot_chart:
                output_file = output_dir / f"genius_shot_chart_{args.match_id}.json"
                shot_chart = GeniusSportsAPI.get_match_shot_chart(str(args.match_id))
                output_file.write_text(
                    json.dumps(shot_chart, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

        elif args.action == "season":
            output_dir = _ensure_output_dir(args.output_dir)
            include_box = args.box_score or args.playbyplay or args.shot_chart
            season_id = args.season_id
            if include_box:
                start_year = _parse_season_start_year(season_id)
                is_historical = start_year is not None and start_year <= 2023
                output_file = (
                    output_dir / f"season_boxscores_{season_id}_{_timestamp()}.json"
                )
                if is_historical:
                    if args.shot_chart:
                        raise ValueError(
                            "Shot chart data is not available for historical BasketHotel seasons."
                        )
                    download_baskethotel_season_boxscores(
                        category_id=args.category_id,
                        season_id=season_id,
                        output_file=str(output_file),
                        limit_games=args.limit_games,
                        max_workers=40,
                        include_playbyplay=args.playbyplay,
                        verbose=True,
                    )
                else:
                    download_matches_with_boxscores(
                        season_id=season_id,
                        category_id=args.category_id,
                        output_file=str(output_file),
                        include_advanced=args.box_score,
                        include_playbyplay=args.playbyplay,
                        include_shot_chart=args.shot_chart,
                        limit_games=args.limit_games,
                        max_workers=40,
                        verbose=True,
                    )
            else:
                output_file = output_dir / f"season_{season_id}_{_timestamp()}.json"
                season_name = None
                try:
                    category_data = BasketFiAPI.get_category(
                        season_id, args.category_id
                    )
                    seasons = category_data.get("category", {}).get("seasons", [])
                    for season in seasons:
                        if season.get("competition_id") == season_id:
                            season_name = season.get("season_name")
                            break
                except Exception:
                    pass
                download_season_comprehensive(
                    category_id=args.category_id,
                    competition_id=season_id,
                    output_file=str(output_file),
                    season_name=season_name,
                    include_advanced=False,
                    max_workers=40,
                    verbose=True,
                )

        elif args.action == "league":
            include_box = args.box_score or args.playbyplay or args.shot_chart
            if args.output_dir:
                output_dir = Path(args.output_dir)
            else:
                output_dir = Path(
                    f"{DEFAULT_OUTPUT_DIR}/league_{args.category_id}_{_timestamp()}"
                )
            output_dir.mkdir(parents=True, exist_ok=True)

            if include_box:
                download_league_boxscores_playbyplay_all_seasons(
                    category_id=args.category_id,
                    output_dir=str(output_dir),
                    start_year=2010,
                    limit_seasons=None,
                    include_playbyplay=args.playbyplay,
                    include_advanced=args.box_score,
                    include_shot_chart=args.shot_chart,
                    max_workers=40,
                    verbose=True,
                )
            else:
                download_league_comprehensive(
                    category_id=args.category_id,
                    output_dir=str(output_dir),
                    season_id=CURRENT_SEASON_ID,
                    include_advanced=False,
                    max_workers=40,
                    verbose=True,
                )

    except Exception as e:
        if isinstance(e, GeniusSportsBoxscoreError):
            url = f" ({e.url})" if e.url else ""
            print(f"Error: {e}{url}")
        else:
            print(f"Error: {e}")
