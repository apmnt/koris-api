#!/usr/bin/env python3
"""
Download all played games for a season and include advanced box scores.

Edit the constants below and run:
  uv run python scripts/get_season_advanced_boxscores.py
"""

from __future__ import annotations

from koris_api import download_matches_with_boxscores

CATEGORY_ID = "4"
SEASON_ID = "2024-2025"
OUTPUT_FILE = "season_boxscores_2024-2025.json"

LIMIT_GAMES = None
MAX_WORKERS = 10
VERBOSE = True


def main() -> None:
    download_matches_with_boxscores(
        season_id=SEASON_ID,
        category_id=CATEGORY_ID,
        output_file=OUTPUT_FILE,
        include_advanced=True,
        limit_games=LIMIT_GAMES,
        max_workers=MAX_WORKERS,
        verbose=VERBOSE,
    )


if __name__ == "__main__":
    main()
