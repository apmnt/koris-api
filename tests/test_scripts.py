"""Script entry points exercise real parsers while replacing their I/O boundaries."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from koris_api.pipeline import Component, resolve_reference
from koris_api.pipeline.types import BatchReport
from scripts import get_genius_boxscore_2514938, get_season_advanced_boxscores
from tests.test_pipeline import FIXTURE_ID
from tests.test_workflows import fetch_fixture_game

ROOT = Path(__file__).resolve().parents[1]


def test_single_game_script_preserves_reported_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.loads(
        (ROOT / "tests/fixtures/genius_sports/livestats_2514938.json").read_text()
    )
    from koris_api.genius_livestats_parser import parse_boxscore

    monkeypatch.setattr(
        get_genius_boxscore_2514938.GeniusSportsAPI,
        "get_match_boxscore",
        lambda match_id: parse_boxscore(raw),
    )
    output = tmp_path / "nested/game.json"
    assert get_genius_boxscore_2514938.main(["--output", str(output)]) == 0
    result = json.loads(output.read_text())
    assert result["teams"][0]["totals"]["Points"] == 80
    assert result["teams"][0]["totals"]["REB"] == 46
    assert sum(p["REB"] for p in result["teams"][0]["players"]) == 42


@pytest.mark.parametrize("failed", [False, True])
def test_season_script_forwards_options_and_returns_failure_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failed: bool
) -> None:
    from dataclasses import replace

    outcome = fetch_fixture_game(resolve_reference(FIXTURE_ID), (Component.BOXSCORE,))
    if failed:
        outcome = replace(outcome, conversion_failure="invalid stats")
    calls: list[dict[str, object]] = []

    def download(**kwargs: object) -> BatchReport:
        calls.append(kwargs)
        return BatchReport((outcome,))

    monkeypatch.setattr(
        get_season_advanced_boxscores, "download_matches_with_boxscores", download
    )
    assert get_season_advanced_boxscores.main(
        [
            "--season-id",
            "huki2627",
            "--limit-games",
            "1",
            "--playbyplay",
            "--shot-chart",
            "--quiet",
            "--output",
            str(tmp_path / "season.json"),
        ]
    ) == int(failed)
    assert calls[0]["season_id"] == "huki2627"
    assert calls[0]["limit_games"] == 1 and calls[0]["include_advanced"] is True
    assert (
        calls[0]["include_playbyplay"] is True
        and calls[0]["include_shot_chart"] is True
    )
    assert calls[0]["verbose"] is False


def test_annotation_script_runs_offline_and_aligns_html_with_parsed_possessions(
    tmp_path: Path,
) -> None:
    from bs4 import BeautifulSoup

    original = ROOT / "tests/fixtures/genius_sports/playbyplay_2701971.html"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/annotate_playbyplay_possessions.py"),
            "--input-html",
            str(original),
            "--output-dir",
            str(tmp_path),
        ],
        check=False,
        timeout=30,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    parsed = json.loads((tmp_path / "playbyplay_2701971_parsed.json").read_text())
    soup = BeautifulSoup(
        (tmp_path / "playbyplay_2701971_annotated.html").read_text(), "html.parser"
    )
    assert (
        len(soup.select(".pbpa.possession-end"))
        == parsed["possessions"]["total_possessions"]
        == 161
    )
    assert len(parsed["events"]) == 564
    assert (
        tmp_path / "playbyplay_2701971_original.html"
    ).read_text() == original.read_text()


@pytest.mark.parametrize(
    "script",
    [
        "scripts/get_genius_boxscore_2514938.py",
        "scripts/get_season_advanced_boxscores.py",
        "scripts/download_1div_advanced_boxscores.py",
        "scripts/annotate_playbyplay_possessions.py",
        "tests/generate_fixtures.py",
    ],
)
def test_script_help_runs_as_an_executable(script: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--help"],
        check=False,
        timeout=30,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("failures", [0, 2])
def test_division_script_reports_partial_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failures: int
) -> None:
    from scripts import download_1div_advanced_boxscores

    calls: list[dict[str, object]] = []

    def download(**kwargs: object) -> int:
        calls.append(kwargs)
        return failures

    monkeypatch.setattr(
        download_1div_advanced_boxscores,
        "download_league_boxscores_all_seasons",
        download,
    )
    assert download_1div_advanced_boxscores.main(
        [
            "--output-dir",
            str(tmp_path),
            "--limit-seasons",
            "1",
            "--start-year",
            "2026",
            "--quiet",
        ]
    ) == int(bool(failures))
    assert calls[0]["category_id"] == "2" and calls[0]["start_year"] == 2026
    assert calls[0]["limit_seasons"] == 1 and calls[0]["verbose"] is False
