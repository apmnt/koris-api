"""Opt-in full-season CLI checks; includes explicit incomplete archival records."""

import json
import os
import time
from pathlib import Path

import pytest

from koris_api import main
from koris_api.seasons import current_season_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.performance,
    pytest.mark.skipif(
        os.getenv("RUN_PERFORMANCE_TESTS") != "1",
        reason="Set RUN_PERFORMANCE_TESTS=1 to run full-season checks",
    ),
]


@pytest.mark.parametrize("season", ["2010-2011", "current", "2024-2025"])
def test_full_season_boxscores_report_every_game(tmp_path: Path, season: str) -> None:
    if season == "current":
        season = os.getenv("KORIS_PERF_MODERN_SEASON", current_season_id())
    started = time.monotonic()
    exit_code = main(
        ["season", "4", season, "--box-score", "--output-dir", str(tmp_path)]
    )
    elapsed = time.monotonic() - started
    if os.getenv("KORIS_PERF_MAX_SECONDS"):
        assert elapsed <= float(os.environ["KORIS_PERF_MAX_SECONDS"])
    data = json.loads((tmp_path / f"season_boxscores_{season}.json").read_text())
    matches = data["matches"]
    assert matches, "Full-season test must not pass on an empty dataset"
    complete = [
        m for m in matches if m.get("boxscore") and not m.get("conversion_error")
    ]
    incomplete = [m for m in matches if m not in complete]
    assert data["metadata"]["matches_with_boxscore"] == sum(
        bool(m.get("boxscore")) for m in matches
    )
    assert data["metadata"]["conversion_failures"] == len(incomplete)
    if season == "2024-2025":
        # The migrated archive currently has games with no player rows. They
        # must be reported as incomplete, never exported as invented zeroes.
        assert complete
        for row in incomplete:
            if row.get("boxscore"):
                assert (
                    "player points do not sum to reported total"
                    in row["conversion_error"]
                )
            else:
                error = row["advanced_boxscore_error"]
                assert error["error_type"] == "invalid_payload"
                assert "players" in error["error_message"]
        assert exit_code == (1 if incomplete else 0)
    else:
        assert exit_code == 0 and not incomplete
    for row in complete:
        teams = row["boxscore"]["teams"]
        assert len(teams) == 2 and all(team["players"] for team in teams)
        assert sum(p["Points"] for p in teams[0]["players"]) == int(row["home_score"])
        assert sum(p["Points"] for p in teams[1]["players"]) == int(row["away_score"])
    print(
        f"{season}: {len(complete)} complete, {len(incomplete)} explicitly incomplete, {elapsed:.1f}s"
    )
