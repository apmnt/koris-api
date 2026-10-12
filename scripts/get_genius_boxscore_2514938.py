#!/usr/bin/env python3
"""Fetch a normalized box score; defaults to the original Genius example game."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from koris_api import GeniusSportsAPI
from koris_api.boxscore_normalizer import normalize_boxscore
from koris_api.pipeline.export import write_json

MATCH_ID = "2514938"
OUTPUT_FILE = "genius_boxscore_2514938.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-id", default=MATCH_ID)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    boxscore = GeniusSportsAPI.get_match_boxscore(args.match_id)
    normalized = normalize_boxscore(boxscore, source="genius")
    if not normalized["teams"] or any(
        not team["players"] for team in normalized["teams"]
    ):
        raise ValueError("Box score has no player data")
    output_path = Path(args.output)
    write_json(output_path, normalized)
    print(f"Saved boxscore for match {args.match_id} to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
