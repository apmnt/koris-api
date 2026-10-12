#!/usr/bin/env python3
"""Download played games with box scores, optionally including events and shots."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from koris_api import download_matches_with_boxscores

CATEGORY_ID = "4"
SEASON_ID = "2024-2025"
OUTPUT_FILE = "season_boxscores_2024-2025.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category-id", default=CATEGORY_ID)
    parser.add_argument("--season-id", default=SEASON_ID)
    parser.add_argument("--output")
    parser.add_argument("--limit-games", type=int)
    parser.add_argument("--max-workers", type=int, default=10)
    parser.add_argument("--playbyplay", action="store_true")
    parser.add_argument("--shot-chart", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output or f"season_boxscores_{args.season_id}.json")
    report = download_matches_with_boxscores(
        season_id=args.season_id,
        category_id=args.category_id,
        output_file=str(output),
        include_advanced=True,
        include_playbyplay=args.playbyplay,
        include_shot_chart=args.shot_chart,
        limit_games=args.limit_games,
        max_workers=args.max_workers,
        verbose=not args.quiet,
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
