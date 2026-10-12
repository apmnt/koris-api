"""Source conventions shared by canonical, legacy, and live-prediction paths."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from koris_api.converters import from_genius_livestats
from koris_api.genius_livestats_parser import parse_playbyplay
from koris_api.live_prediction import (
    _build_live_match,
    _live_events_from_data,
    detect_season_files,
    downsample_indices,
    fit_point_diff_scale,
    normalize_score_diff_probability,
    smooth_step_series,
)
from koris_api.seasons import season_id_for_date


def overtime_data() -> dict[str, object]:
    return {
        "period": 2,
        "periodType": "OVERTIME",
        "clock": "04:30",
        "tm": {"1": {"name": "Home", "score": 80}, "2": {"name": "Away", "score": 80}},
        "pbp": [
            {
                "actionNumber": 1,
                "actionType": "2pt",
                "period": 2,
                "periodType": "OVERTIME",
                "gt": "04:30:00",
                "tno": 1,
                "success": None,
                "s1": 80,
                "s2": 80,
            }
        ],
    }


def test_second_overtime_is_consistent_across_adapters() -> None:
    data = overtime_data()
    canonical = from_genius_livestats(data, match_id="1", regulation_periods=4)
    assert canonical.events[0].period == 6
    assert parse_playbyplay(data)["events"][0]["period"] == "OT2"
    assert _live_events_from_data(data)[0]["period"] == "OT2"
    assert not canonical.events[0].HasField("made")
    assert "missed" not in str(parse_playbyplay(data)["events"][0]["action"])


def test_live_overtime_elapsed_time_uses_full_game_period_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "koris_api.live_prediction._fetch_json", lambda url: overtime_data()
    )
    result = _build_live_match(
        match_id="1",
        competition_id="unused",
        date="2026-10-01",
        time="18:30",
        season="2026-2027",
        home_team="Home",
        away_team="Away",
        matches=[],
        live_data_url=None,
    )
    assert result["_current_elapsed_seconds"] == 2730  # 40 min + first OT + 30 sec
    assert result["home_score"] == result["away_score"] == 80


def test_step_series_preserves_same_second_final_value_and_fills_gaps() -> None:
    rows = pd.DataFrame({"elapsed_seconds": [0, 2, 2, 4], "event_order": [0, 1, 2, 3]})
    probabilities = np.array([0.5, 0.4, 0.6, 0.8])
    seconds, values = smooth_step_series(
        rows, probabilities, total_seconds=4, window_seconds=1
    )
    np.testing.assert_array_equal(seconds, [0, 1, 2, 3, 4])
    np.testing.assert_allclose(values, [0.5, 0.5, 0.6, 0.6, 0.8])


def test_probability_curve_is_bounded_symmetric_and_fit_recovers_scale() -> None:
    diff = np.array([-10.0, -4.0, 0.0, 4.0, 10.0])
    target = normalize_score_diff_probability(diff, 4)
    assert np.all((target > 0) & (target < 1))
    assert target[2] == 0.5
    np.testing.assert_allclose(target + target[::-1], np.ones(5))
    scale, curve = fit_point_diff_scale(
        diff,
        target,
        default_scale=8,
        scale_min=2,
        scale_max=6,
        scale_step=1,
        fit_enabled=True,
    )
    assert scale == 4
    np.testing.assert_allclose(curve, target)


def test_downsample_always_keeps_terminal_second() -> None:
    assert downsample_indices(12, 5) == [0, 5, 10, 12]
    assert downsample_indices(0, 5) == [0]


def test_season_file_selection_uses_years_and_filters_other_leagues(
    tmp_path: Path,
) -> None:
    for name in (
        "1div_25-26.json",
        "1div_23-24.json",
        "1div_24-25.json",
        "koris_25-26.json",
        "1div_notes.json",
    ):
        (tmp_path / name).write_text("{}")
    selected = detect_season_files(
        data_dir=tmp_path, league_prefix="1div", latest_seasons=2
    )
    assert [p.name for p in selected] == ["1div_24-25.json", "1div_25-26.json"]


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 6, 30), "huki2526"),
        (date(2026, 7, 1), "huki2627"),
        (date(2027, 1, 1), "huki2627"),
    ],
)
def test_season_rollover_has_an_explicit_calendar_input(
    day: date, expected: str
) -> None:
    assert season_id_for_date(day) == expected
