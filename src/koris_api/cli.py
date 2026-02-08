import argparse
import curses
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .basketfi_api import BasketFiAPI
from .boxscore_normalizer import normalize_boxscore
from .genius_api import GeniusSportsAPI, GeniusSportsBoxscoreError
from .services.baskethotel_season_boxscores import (
    download_baskethotel_season_boxscores,
)
from .services.common import _augment_seasons_with_baskethotel
from .services.league_boxscores import (
    download_league_all_seasons,
    download_league_boxscores_all_seasons,
)
from .services.league_comprehensive import download_league_comprehensive
from .services.season_advanced_averages import download_season_advanced_averages
from .services.season_boxscores import (
    download_matches_with_boxscores,
    retry_advanced_boxscores_404s,
)
from .services.season_comprehensive import download_season_comprehensive
from .services.season_game_leaders import download_season_game_leaders
from .services.team_season import download_team_season


def main() -> None:
    """CLI entry point for koris-api."""
    epilog = """
examples:
  # Option 1: All teams with their matches from one season
  uv run koris-api season-comprehensive --category-id 4 --season-id huki2526 --output season.json
  
  # Option 1b: Match list with boxscores (optionally limit games)
  uv run koris-api season-boxscores --category-id 4 --season-id 2024-2025 --adv-players --limit-games 1 --output season_boxscores.json

  # Option 2: All matches of one team from one season
  uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output team.json

  # Option 2b: Historical season boxscores (BasketHotel)
  uv run koris-api season-baskethotel-boxscores --category-id 4 --season-id 2015-2016 --output season_boxscores.json

  # Option 3: All seasons with all teams and their matches
  uv run koris-api league-comprehensive --category-id 4 --output-dir korisliiga_data

  # Option 6: All seasons from 2010 with player boxscores
  uv run koris-api league-boxscores-all-seasons --category-id 4 --output-dir korisliiga_boxscores

  # Option 4: Season averages for rebounds/assists/steals (Genius Sports boxscores)
  uv run koris-api season-advanced-averages --category-id 4 --season-id huki2526 --all-seasons --output season_avgs.json

  # Option 5: Historical season game leaders (BasketHotel boxscores)
  uv run koris-api season-game-leaders --category-id 4 --season-id 2015-2016 --output season_leaders.json

  # Option 7: Retry advanced boxscores that failed with 404
  uv run koris-api retry-advanced-404s --input season_boxscores_2023-2024.json --output season_boxscores_2023-2024_retry.json

  # Genius Sports: fetch a single match boxscore
  uv run koris-api genius match 2514938 --output genius_match_2514938.json

  # Add --adv-players to include per-match player stats from advanced boxscores
  # Add --adv-teams to include team season statistics (averages, shooting, totals)
  # Add --cache-file to reuse boxscore summaries across runs

common category IDs:
  4  - Korisliiga (Men's top division)
  2  - Miesten I divisioona A (Men's 1st division A)
  13 - Naisten Korisliiga (Women's top division)

notes:
  All commands save data to a single structured JSON file.
  Use --adv-players for per-match player statistics from advanced boxscores.
  Use --adv-teams for team season statistics (requires Genius Sports competition ID).
  For team-season action, category-id is optional and will be auto-detected from team's matches.
"""

    parser = argparse.ArgumentParser(
        description="Access Koris API from command line",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=[
            "season-comprehensive",
            "season-boxscores",
            "season-baskethotel-boxscores",
            "team-season",
            "league-comprehensive",
            "league-boxscores-all-seasons",
            "season-advanced-averages",
            "season-game-leaders",
            "retry-advanced-404s",
            "genius",
        ],
        help="Action to perform",
    )
    parser.add_argument(
        "subaction",
        nargs="?",
        help="Sub-action for action=genius (e.g., match)",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Target for sub-action (e.g., match ID)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for options interactively",
    )
    parser.add_argument(
        "--season-id",
        default="huki2526",
        help="Season ID (default: huki2526 for current season)",
    )
    parser.add_argument(
        "--season-ids",
        help="Comma-separated season IDs to process (overrides --season-id)",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2010,
        help="Start year for all-seasons downloads (default: 2010)",
    )
    parser.add_argument(
        "--limit-seasons",
        type=int,
        help="Limit the number of most recent seasons to download (all-seasons actions)",
    )
    parser.add_argument(
        "--combine-output",
        action="store_true",
        help="Combine season outputs into a single JSON file (all-seasons actions)",
    )
    parser.add_argument(
        "--combined-file",
        help="Path for combined output JSON when --combine-output is set",
    )
    parser.add_argument(
        "--category-id",
        help="Category ID (default: 4 for Korisliiga, optional for team-season - will be auto-detected)",
    )
    parser.add_argument(
        "--competition-id",
        help="Genius Sports competition ID (for players-season and players-team)",
    )
    parser.add_argument(
        "--team-id",
        help="Team ID (for team-season)",
    )
    parser.add_argument(
        "--match-id",
        help="Genius Sports match ID (for playbyplay)",
    )
    parser.add_argument(
        "--genius-competition-id",
        help="Genius Sports competition ID (required for --adv-teams)",
    )
    parser.add_argument(
        "--genius-team-id",
        help="Genius Sports team ID (required for --adv-teams)",
    )
    parser.add_argument(
        "--old-season-id",
        default="121333",
        help="BasketHotel season ID for old games (default: 121333)",
    )
    parser.add_argument(
        "--old-league-id",
        default="2",
        help="BasketHotel league ID for old games (default: 2)",
    )
    parser.add_argument(
        "--output",
        help="Output file path (auto-generated if not specified)",
    )
    parser.add_argument(
        "--input",
        help="Input JSON file (retry-advanced-404s)",
    )
    parser.add_argument(
        "--limit-games",
        type=int,
        help="Limit number of games fetched (useful for samples)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for comprehensive downloads (for league-comprehensive)",
    )
    parser.add_argument(
        "--adv-players",
        action="store_true",
        help="Include advanced boxscores with player data from Genius Sports (per-match stats)",
    )
    parser.add_argument(
        "--adv-teams",
        action="store_true",
        help="Include team season statistics (averages, shooting, totals) from Genius Sports",
    )
    parser.add_argument(
        "--all-seasons",
        action="store_true",
        help="Include all seasons for the category (season-advanced-averages)",
    )
    parser.add_argument(
        "--cache-file",
        help="Cache file for advanced boxscore summaries (season-advanced-averages)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=40,
        help="Concurrent workers for advanced stats (default: 40)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    def _select_single_curses(
        title: str, options: list[tuple[str, str]], default: Optional[str]
    ) -> Optional[str]:
        def _run(stdscr):
            curses.curs_set(0)
            current = 0
            if default:
                for idx, (_, value) in enumerate(options):
                    if value == default:
                        current = idx
                        break
            while True:
                stdscr.erase()
                stdscr.addstr(0, 0, title)
                stdscr.addstr(1, 0, "Use arrows to move, Enter to select.")
                for idx, (label, _) in enumerate(options):
                    marker = "➤ " if idx == current else "  "
                    if idx == current:
                        stdscr.attron(curses.A_REVERSE)
                    stdscr.addstr(3 + idx, 0, f"{marker}{label}")
                    if idx == current:
                        stdscr.attroff(curses.A_REVERSE)
                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    current = (current - 1) % len(options)
                elif key in (curses.KEY_DOWN, ord("j")):
                    current = (current + 1) % len(options)
                elif key in (curses.KEY_ENTER, 10, 13):
                    return options[current][1]
                elif key in (27, ord("q")):
                    return None

        try:
            return curses.wrapper(_run)
        except curses.error:
            return None

    def _select_multi_curses(
        title: str, options: list[tuple[str, str]], defaults: list[str]
    ) -> Optional[list[str]]:
        def _run(stdscr):
            curses.curs_set(0)
            current = 0
            selected = {value for value in defaults}
            while True:
                stdscr.erase()
                stdscr.addstr(0, 0, title)
                stdscr.addstr(
                    1, 0, "Use arrows to move, Space to toggle, Enter to confirm."
                )
                for idx, (label, value) in enumerate(options):
                    checked = "[x]" if value in selected else "[ ]"
                    marker = "➤ " if idx == current else "  "
                    line = f"{marker}{checked} {label}"
                    if idx == current:
                        stdscr.attron(curses.A_REVERSE)
                    stdscr.addstr(3 + idx, 0, line)
                    if idx == current:
                        stdscr.attroff(curses.A_REVERSE)
                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    current = (current - 1) % len(options)
                elif key in (curses.KEY_DOWN, ord("j")):
                    current = (current + 1) % len(options)
                elif key == ord(" "):
                    value = options[current][1]
                    if value in selected:
                        selected.remove(value)
                    else:
                        selected.add(value)
                elif key in (curses.KEY_ENTER, 10, 13):
                    return list(selected)
                elif key in (27, ord("q")):
                    return None

        try:
            return curses.wrapper(_run)
        except curses.error:
            return None

    def _prompt_single(
        title: str, options: list[tuple[str, str]], default: Optional[str]
    ) -> str:
        if sys.stdin.isatty() and options:
            selected = _select_single_curses(title, options, default)
            if selected is not None:
                return selected
        print(title)
        default_index = 1
        for idx, (label, value) in enumerate(options, start=1):
            print(f"  {idx}) {label}")
            if value == default:
                default_index = idx
        while True:
            choice = input(f"Select [default {default_index}]: ").strip()
            if not choice:
                return options[default_index - 1][1]
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                return options[int(choice) - 1][1]
            print("Enter a valid number.")

    def _prompt_multi(
        title: str, options: list[tuple[str, str]], defaults: list[str]
    ) -> list[str]:
        if sys.stdin.isatty() and options:
            selected = _select_multi_curses(title, options, defaults)
            if selected is not None:
                return selected
        print(title)
        default_indices = []
        for idx, (label, value) in enumerate(options, start=1):
            print(f"  {idx}) {label}")
            if value in defaults:
                default_indices.append(str(idx))
        prompt = f"Select (space-separated) [default {' '.join(default_indices) or 'none'}]: "
        while True:
            choice = input(prompt).strip()
            if not choice:
                return defaults
            tokens = choice.split()
            if all(t.isdigit() and 1 <= int(t) <= len(options) for t in tokens):
                return [options[int(t) - 1][1] for t in tokens]
            print("Enter space-separated numbers.")

    def _prompt_text(label: str, default: Optional[str], required: bool = False) -> str:
        prompt = f"{label} [{default}]: " if default else f"{label}: "
        while True:
            value = input(prompt).strip()
            if value:
                return value
            if default is not None:
                return default
            if not required:
                return ""
            print("Value required.")

    def _load_categories() -> list[tuple[str, str]]:
        categories_path = Path(__file__).parent.parent.parent / "categories.json"
        if categories_path.exists():
            data = json.loads(categories_path.read_text(encoding="utf-8"))
            return [
                (f"{cid}: {cdata.get('category_name', cid)}", str(cid))
                for cid, cdata in data.items()
            ]
        return [
            ("4: Korisliiga (Men's top division)", "4"),
            ("2: Miesten I divisioona A", "2"),
            ("13: Naisten Korisliiga", "13"),
        ]

    def _load_seasons(category_id: str) -> list[tuple[str, str]]:
        try:
            category_data = BasketFiAPI.get_category("huki2526", category_id)
            seasons = category_data.get("category", {}).get("seasons", [])
            seasons = _augment_seasons_with_baskethotel(seasons, category_id)
            return [
                (
                    f"{s.get('season_name', s.get('competition_id'))} ({s.get('competition_id')})",
                    str(s.get("competition_id") or s.get("season_id")),
                )
                for s in seasons
            ]
        except Exception:
            return []

    def _interactive(args: argparse.Namespace) -> argparse.Namespace:
        mode = _prompt_single(
            "Select download scope:",
            [("Season", "season"), ("League", "league")],
            "season",
        )

        categories = _load_categories()
        category_default = args.category_id or "4"
        args.category_id = _prompt_single(
            "Select league:", categories, category_default
        )

        season_default = args.season_id or "huki2526"
        args.season_ids = None
        if mode == "season":
            args.action = "season-boxscores"
            seasons = _load_seasons(args.category_id) if args.category_id else []
            if seasons:
                season_values = _prompt_multi(
                    "Select season(s):",
                    seasons,
                    [season_default],
                )
                if season_values:
                    args.season_ids = ",".join(season_values)
                    args.season_id = season_values[0]
            else:
                args.season_id = _prompt_text(
                    "Season ID", season_default, required=True
                )
        else:
            args.action = "league-comprehensive"
            args.season_id = season_default

        advanced = _prompt_single(
            "Download advanced boxscore?",
            [("Yes", "yes"), ("No", "no")],
            "no",
        )
        args.adv_players = advanced == "yes"
        args.adv_teams = False
        args.all_seasons = False
        args.combine_output = False

        return args

    if args.action is None or args.interactive:
        args = _interactive(args)

    season_ids = []
    if args.season_ids:
        season_ids = [s.strip() for s in args.season_ids.split(",") if s.strip()]

    def _output_for_season(base: Optional[str], season_id: str, prefix: str) -> str:
        if not base:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return f"{prefix}_{season_id}_{timestamp}.json"
        path = Path(base)
        if path.suffix:
            return str(path.with_name(f"{path.stem}_{season_id}{path.suffix}"))
        return f"{base}_{season_id}.json"

    try:
        # Provide default category_id if not specified (except for team-season which auto-detects)
        if args.action not in {"team-season", "genius"} and not args.category_id:
            args.category_id = "4"  # Default to Korisliiga

        # Option 1: All teams with their matches from one season
        if args.action == "season-comprehensive":
            # For season-comprehensive, competition_id is required (it's the season identifier)
            if not args.season_id:
                print("Error: --season-id is required for season-comprehensive action")
                print(
                    "Example: uv run koris-api season-comprehensive --category-id 4 --season-id huki2526 --output season.json"
                )
                return
            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(args.output, season_id, "season")

                # Get season name from category data if possible
                season_name = None
                try:
                    category_data = BasketFiAPI.get_category(
                        season_id, args.category_id
                    )
                    if (
                        "category" in category_data
                        and "seasons" in category_data["category"]
                    ):
                        for season in category_data["category"]["seasons"]:
                            if season.get("competition_id") == season_id:
                                season_name = season.get("season_name")
                                break
                except Exception:
                    pass  # Use competition_id as season_name if we can't get it

                download_season_comprehensive(
                    category_id=args.category_id,
                    competition_id=season_id,
                    output_file=output_file,
                    season_name=season_name,
                    include_advanced=args.adv_players,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 1b: Match list with boxscores (optional limit)
        elif args.action == "season-boxscores":
            if not args.season_id:
                print("Error: --season-id is required for season-boxscores action")
                print(
                    "Example: uv run koris-api season-boxscores --category-id 4 --season-id 2024-2025 --adv-players --limit-games 1 --output season_boxscores.json"
                )
                return
            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_boxscores"
                )
                download_matches_with_boxscores(
                    season_id=season_id,
                    category_id=args.category_id,
                    output_file=output_file,
                    include_advanced=args.adv_players,
                    limit_games=args.limit_games,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 2: All matches of one team from one season
        elif args.action == "team-season":
            if not args.team_id:
                print("Error: --team-id is required for team-season action")
                print(
                    "Example: uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output team.json"
                )
                return

            if not args.season_id:
                print("Error: --season-id is required for team-season action")
                print(
                    "Example: uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output team.json"
                )
                return

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, f"{args.team_id}_{season_id}", "team"
                )

                # Get season name from category data if possible (and if category_id is provided)
                season_name = None
                if args.category_id:
                    try:
                        category_data = BasketFiAPI.get_category(
                            season_id, args.category_id
                        )
                        if (
                            "category" in category_data
                            and "seasons" in category_data["category"]
                        ):
                            for season in category_data["category"]["seasons"]:
                                if season.get("competition_id") == season_id:
                                    season_name = season.get("season_name")
                                    break
                    except Exception:
                        pass

                download_team_season(
                    team_id=args.team_id,
                    category_id=args.category_id,
                    competition_id=season_id,
                    output_file=output_file,
                    season_name=season_name,
                    include_advanced=args.adv_players,
                    include_team_stats=args.adv_teams,
                    genius_competition_id=args.genius_competition_id,
                    genius_team_id=args.genius_team_id,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 2b: BasketHotel boxscores for a historical season
        elif args.action == "season-baskethotel-boxscores":
            if not args.category_id:
                print(
                    "Error: --category-id is required for season-baskethotel-boxscores"
                )
                print(
                    "Example: uv run koris-api season-baskethotel-boxscores --category-id 4 --season-id 2015-2016 --output season_boxscores.json"
                )
                return

            if not args.season_id:
                print("Error: --season-id is required for season-baskethotel-boxscores")
                return

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_baskethotel_boxscores"
                )
                download_baskethotel_season_boxscores(
                    category_id=args.category_id,
                    season_id=season_id,
                    output_file=output_file,
                    limit_games=args.limit_games,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 3: All seasons with all teams and their matches
        elif args.action == "league-comprehensive":
            # Validate output directory
            if not args.output_dir:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                args.output_dir = f"league_{args.category_id}_{timestamp}"

            download_league_comprehensive(
                category_id=args.category_id,
                output_dir=args.output_dir,
                season_id=args.season_id,
                include_advanced=args.adv_players,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

        # Option 3b: All seasons from start year with player boxscores
        elif args.action == "league-boxscores-all-seasons":
            if not args.output_dir:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                args.output_dir = f"league_boxscores_{args.category_id}_{timestamp}"

            download_league_boxscores_all_seasons(
                category_id=args.category_id,
                output_dir=args.output_dir,
                start_year=args.start_year,
                limit_seasons=args.limit_seasons,
                combine_output=args.combine_output,
                combined_file=args.combined_file,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )

        # Option 4: Season averages for rebounds/assists/steals from advanced boxscores
        elif args.action == "season-advanced-averages":
            if not args.category_id:
                print("Error: --category-id is required for season-advanced-averages")
                print(
                    "Example: uv run koris-api season-advanced-averages --category-id 4 --season-id huki2526 --all-seasons --output season_avgs.json"
                )
                return

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_advanced_averages"
                )
                download_season_advanced_averages(
                    category_id=args.category_id,
                    season_id=season_id,
                    output_file=output_file,
                    all_seasons=args.all_seasons,
                    cache_file=args.cache_file,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )

        # Option 5: Historical season game leaders from BasketHotel boxscores
        elif args.action == "season-game-leaders":
            if not args.category_id:
                print("Error: --category-id is required for season-game-leaders")
                print(
                    "Example: uv run koris-api season-game-leaders --category-id 4 --season-id 2015-2016 --output season_leaders.json"
                )
                return

            if not args.season_id:
                print("Error: --season-id is required for season-game-leaders")
                return

            season_targets = season_ids or [args.season_id]
            for season_id in season_targets:
                output_file = _output_for_season(
                    args.output, season_id, "season_game_leaders"
                )
                download_season_game_leaders(
                    category_id=args.category_id,
                    season_id=season_id,
                    output_file=output_file,
                    max_workers=args.concurrency,
                    verbose=not args.quiet,
                )
        elif args.action == "retry-advanced-404s":
            if not args.input:
                print("Error: --input is required for retry-advanced-404s")
                print(
                    "Example: uv run koris-api retry-advanced-404s --input season_boxscores_2023-2024.json"
                )
                return

            retry_advanced_boxscores_404s(
                input_file=args.input,
                output_file=args.output,
                max_workers=args.concurrency,
                verbose=not args.quiet,
            )
        elif args.action == "genius":
            if args.subaction != "match":
                print("Error: genius requires sub-action 'match'")
                print("Example: uv run koris-api genius match 2514938")
                return

            match_id = args.target or args.match_id
            if not match_id:
                print("Error: match ID is required")
                print("Example: uv run koris-api genius match 2514938")
                return

            output_file = args.output or f"genius_match_{match_id}.json"
            boxscore = GeniusSportsAPI.get_match_boxscore(
                str(match_id),
                not_found_retries=0,
            )
            normalized = normalize_boxscore(boxscore, source="genius")

            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(normalized, f, indent=2, ensure_ascii=False)

            if not args.quiet:
                print(f"Saved Genius Sports boxscore to {output_path.absolute()}")

    except Exception as e:
        if isinstance(e, GeniusSportsBoxscoreError):
            url = f" ({e.url})" if e.url else ""
            print(f"Error: {e}{url}")
        else:
            print(f"Error: {e}")
