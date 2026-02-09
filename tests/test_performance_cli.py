"""
Performance tests for CLI season boxscore downloads.

These are opt-in because they fetch full seasons with advanced stats.
Run with: RUN_PERFORMANCE_TESTS=1 uv run pytest tests/test_performance_cli.py -v
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from koris_api import main


pytestmark = [pytest.mark.integration, pytest.mark.performance]


def _require_perf_opt_in() -> None:
    if os.environ.get("RUN_PERFORMANCE_TESTS") != "1":
        pytest.skip("Set RUN_PERFORMANCE_TESTS=1 to run performance tests.")


def _run_cli(args: list[str]) -> float:
    start = time.monotonic()
    original_argv = sys.argv[:]
    try:
        sys.argv = ["koris-api", *args]
        main()
    finally:
        sys.argv = original_argv
    return time.monotonic() - start


def _assert_max_duration(elapsed: float, env_key: str) -> None:
    limit = os.environ.get(env_key)
    if not limit:
        return
    max_seconds = float(limit)
    assert elapsed <= max_seconds, f"{env_key} exceeded: {elapsed:.1f}s > {max_seconds:.1f}s"


def test_baskethotel_season_2010_boxscores(tmp_path: Path) -> None:
    _require_perf_opt_in()
    output_file = tmp_path / "season_2010_baskethotel.json"
    elapsed = _run_cli(
        [
            "season-baskethotel-boxscores",
            "--category-id",
            "4",
            "--season-id",
            "2010-2011",
            "--output",
            str(output_file),
        ]
    )
    _assert_max_duration(elapsed, "KORIS_PERF_MAX_SECONDS_2010")

    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert data["metadata"]["source"] == "baskethotel"
    assert data["matches"], "Expected matches for 2010 season"
    assert data["metadata"]["matches_with_boxscore"] > 0


def test_genius_season_2024_boxscores(tmp_path: Path) -> None:
    _require_perf_opt_in()
    output_file = tmp_path / "season_2024_genius.json"
    elapsed = _run_cli(
        [
            "season-boxscores",
            "--category-id",
            "4",
            "--season-id",
            "2024-2025",
            "--adv-players",
            "--output",
            str(output_file),
        ]
    )
    _assert_max_duration(elapsed, "KORIS_PERF_MAX_SECONDS_2024")

    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert data["metadata"]["source"] == "genius"
    assert data["matches"], "Expected matches for 2024 season"
    assert data["metadata"]["matches_with_boxscore"] > 0
