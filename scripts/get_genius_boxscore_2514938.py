#!/usr/bin/env python3
"""
Fetch a single Genius Sports boxscore (match 2514938) and save to JSON.

Run:
  uv run python scripts/get_genius_boxscore_2514938.py
"""

from __future__ import annotations

import json
from pathlib import Path

from koris_api import GeniusSportsAPI
from koris_api.boxscore_normalizer import normalize_boxscore

MATCH_ID = "2514938"
OUTPUT_FILE = "genius_boxscore_2514938.json"


def main() -> None:
    boxscore = GeniusSportsAPI.get_match_boxscore(MATCH_ID)
    normalized = normalize_boxscore(boxscore, source="genius")

    output_path = Path(OUTPUT_FILE)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)

    print(f"Saved boxscore for match {MATCH_ID} to {output_path}")


if __name__ == "__main__":
    main()
