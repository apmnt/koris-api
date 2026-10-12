import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

from .basketfi_api import BasketFiAPI
from .data import fields, records, text
from .genius_api import GeniusSportsBoxscoreError
from .pipeline import Component, Provider, fetch_game, resolve_reference
from .pipeline.export import write_game, write_json
from .pipeline.games import export_legacy
from .seasons import current_season_id
from .services.baskethotel_season_boxscores import (
    download_baskethotel_season_boxscores,
)
from .services.canonical_games import (
    download_canonical_league,
    download_canonical_season,
)
from .services.league_boxscores import (
    download_league_boxscores_playbyplay_all_seasons,
)
from .services.league_comprehensive import download_league_comprehensive
from .services.season_boxscores import download_matches_with_boxscores
from .services.season_comprehensive import download_season_comprehensive

DEFAULT_OUTPUT_DIR = "output"


class Arguments(argparse.Namespace):
    """Types and defaults for the arguments shared by the three commands."""

    action: str = ""
    format: str = "legacy"
    box_score: bool = False
    playbyplay: bool = False
    shot_chart: bool = False
    output_dir: str | None = None
    limit_games: int | None = None
    match_id: str = ""
    provider: str | None = None
    competition_id: str | None = None
    league_id: str | None = None
    season_id: str = ""
    category_id: str = ""
    clock_mode: str | None = None


def _download_match(args: Arguments, requested: tuple[Component, ...]) -> int:
    output_dir = _ensure_output_dir(args.output_dir)
    reference = resolve_reference(
        args.match_id,
        provider=Provider(args.provider) if args.provider else None,
        competition_id=args.competition_id,
        league_id=args.league_id,
        season_id=args.season_id or None,
        clock_mode=args.clock_mode,
    )
    outcome = fetch_game(reference, requested, cache_dir=output_dir / ".cache")
    if args.format == "legacy":
        values = export_legacy(outcome, {})
        prefixes = {
            Component.BOXSCORE: "genius_match",
            Component.PLAYBYPLAY: "genius_playbyplay",
            Component.SHOTS: "genius_shot_chart",
        }
        for component in requested:
            if component.value in values:
                write_json(
                    output_dir / f"{prefixes[component]}_{args.match_id}.json",
                    values[component.value],
                )
    elif outcome.game is not None:
        suffix = ".pb" if args.format == "protobuf" else ".pb.json"
        write_game(
            output_dir / f"game_{args.match_id}{suffix}",
            outcome.game,
            binary=args.format == "protobuf",
        )
    if outcome.complete:
        return 0
    failures = [f.failure.message for f in outcome.fetches if f.failure is not None]
    failures.extend(outcome.issues)
    if outcome.conversion_failure:
        failures.append(outcome.conversion_failure)
    print("Error: " + "; ".join(failures), file=sys.stderr)
    return 1


def _parse_season_start_year(season_id: str) -> int | None:
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


def _ensure_output_dir(output_dir: str | None) -> Path:
    base = Path(output_dir or DEFAULT_OUTPUT_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base


def main() -> int:
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
    _ = shared.add_argument(
        "--format",
        choices=("legacy", "protobuf-json", "protobuf"),
        default="legacy",
        help="Game export format (match and season)",
    )
    _ = shared.add_argument(
        "--box-score",
        action="store_true",
        help="Include box score data",
    )
    _ = shared.add_argument(
        "--playbyplay",
        action="store_true",
        help="Include play-by-play data",
    )
    _ = shared.add_argument(
        "--shot-chart",
        action="store_true",
        help="Include shot chart data (Genius data.json feed)",
    )
    _ = shared.add_argument(
        "--output-dir",
        help="Directory for output files (default: ./output)",
    )
    _ = shared.add_argument(
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
    _ = match_parser.add_argument("match_id", help="Provider game ID")
    _ = match_parser.add_argument(
        "--provider", choices=("genius", "genius_html", "sportradar", "baskethotel")
    )
    _ = match_parser.add_argument("--competition-id")
    _ = match_parser.add_argument("--league-id")
    _ = match_parser.add_argument("--season-id")
    _ = match_parser.add_argument("--clock-mode", choices=("remaining",))

    season_parser = subparsers.add_parser("season", parents=[shared])
    _ = season_parser.add_argument("category_id", help="Category ID")
    _ = season_parser.add_argument("season_id", help="Season ID")

    league_parser = subparsers.add_parser("league", parents=[shared])
    _ = league_parser.add_argument("category_id", help="Category ID")

    args = Arguments()
    _ = parser.parse_args(namespace=args)

    try:
        requested = tuple(
            component
            for component, enabled in (
                (
                    Component.BOXSCORE,
                    args.box_score or not (args.playbyplay or args.shot_chart),
                ),
                (Component.PLAYBYPLAY, args.playbyplay),
                (Component.SHOTS, args.shot_chart),
            )
            if enabled
        )
        if args.action == "match":
            return _download_match(args, requested)
        if args.format != "legacy":
            output_dir = _ensure_output_dir(args.output_dir)
            if args.action == "season":
                report = download_canonical_season(
                    args.season_id,
                    args.category_id,
                    output_dir / args.season_id,
                    requested,
                    binary=args.format == "protobuf",
                    limit_games=args.limit_games,
                    max_workers=40,
                )
                return 1 if report.failed else 0
            report = download_canonical_league(
                args.category_id,
                output_dir,
                requested,
                binary=args.format == "protobuf",
                limit_games=args.limit_games,
                max_workers=40,
            )
            return 1 if report.failed else 0
        if args.action == "season":
            output_dir = _ensure_output_dir(args.output_dir)
            include_box = args.box_score or args.playbyplay or args.shot_chart
            season_id = args.season_id
            if include_box:
                start_year = _parse_season_start_year(season_id)
                is_historical = start_year is not None and start_year <= 2023
                output_file = output_dir / f"season_boxscores_{season_id}.json"
                if is_historical:
                    _ = download_baskethotel_season_boxscores(
                        category_id=args.category_id,
                        season_id=season_id,
                        output_file=str(output_file),
                        limit_games=args.limit_games,
                        max_workers=40,
                        include_playbyplay=args.playbyplay,
                        include_shot_chart=args.shot_chart,
                        verbose=True,
                    )
                else:
                    _ = download_matches_with_boxscores(
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
                    category_data = fields(
                        BasketFiAPI.get_category(season_id, args.category_id)
                    )
                    seasons = records(
                        fields(category_data.get("category")).get("seasons")
                    )
                    for season in seasons:
                        if season.get("competition_id") == season_id:
                            season_name = text(season.get("season_name"))
                            break
                except Exception:
                    logging.getLogger(__name__).debug(
                        "cli: operation failed", exc_info=True
                    )
                download_season_comprehensive(
                    category_id=args.category_id,
                    competition_id=season_id,
                    output_file=str(output_file),
                    season_name=season_name,
                    include_advanced=False,
                    max_workers=40,
                    verbose=True,
                )

            if output_file.exists():
                saved = fields(
                    cast(object, json.loads(output_file.read_text(encoding="utf-8")))
                )
                metadata = fields(saved.get("metadata"))
                if any(
                    metadata.get(field, 0)
                    for field in (
                        "matches_failed",
                        "matches_playbyplay_failed",
                        "matches_shot_chart_failed",
                        "conversion_failures",
                        "teams_failed",
                    )
                ):
                    return 1

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
                report = download_league_boxscores_playbyplay_all_seasons(
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
                if report.failed:
                    return 1
            else:
                summary = download_league_comprehensive(
                    category_id=args.category_id,
                    output_dir=str(output_dir),
                    season_id=current_season_id(),
                    include_advanced=False,
                    max_workers=40,
                    verbose=True,
                )
                if any(
                    fields(fields(summary).get("metadata")).get(field, 0)
                    for field in (
                        "seasons_failed",
                        "teams_failed",
                        "matches_failed",
                        "conversion_failures",
                    )
                ):
                    return 1

        return 0
    except Exception as e:
        logging.getLogger(__name__).debug("cli: operation failed", exc_info=True)
        if isinstance(e, GeniusSportsBoxscoreError):
            url = f" ({e.url})" if e.url else ""
            print(f"Error: {e}{url}", file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1
