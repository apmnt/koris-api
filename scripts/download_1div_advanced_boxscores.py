#!/usr/bin/env python3
"""
Download all seasons for Miesten I divisioona A with advanced boxscores.

This uses the KorisAPI helpers in src/koris_api to fetch:
  - Historical seasons (pre-2022) via BasketHotel (boxscores with players)
  - Modern seasons (2022+) via Genius Sports advanced boxscores
"""

from __future__ import annotations

import argparse
from pathlib import Path

from koris_api import download_league_boxscores_all_seasons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download all seasons for Miesten I divisioona A (category 2) "
            "with advanced boxscores."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="data/miesten_i_divisioona_a_boxscores",
        help="Directory for per-season JSON outputs.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2010,
        help="First season year to include (based on season start year).",
    )
    parser.add_argument(
        "--limit-seasons",
        type=int,
        default=None,
        help="Optional limit for number of seasons to download.",
    )
    parser.add_argument(
        "--combine-output",
        action="store_true",
        help="Combine all seasons into a single JSON file.",
    )
    parser.add_argument(
        "--combined-file",
        default=None,
        help="Output file for combined JSON (only used with --combine-output).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Max concurrent workers for downloads.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    download_league_boxscores_all_seasons(
        category_id="2",
        output_dir=str(output_dir),
        start_year=args.start_year,
        limit_seasons=args.limit_seasons,
        combine_output=args.combine_output,
        combined_file=args.combined_file,
        max_workers=args.max_workers,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
