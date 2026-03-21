import pandas as pd

from koris_api.win_probability import (
    _seconds_remaining,
    _trim_events_to_terminal_game,
    fit_bucketed_model,
    predict_state_probabilities,
)


def test_seconds_remaining_handles_regulation_and_overtime() -> None:
    assert _seconds_remaining("P1", "09:59:00") == 2399
    assert _seconds_remaining("P4", "00:10:00") == 10
    assert _seconds_remaining("OT2", "04:30:00") == 270


def test_trim_events_to_terminal_game_drops_junk_after_final_marker() -> None:
    events = [
        {"event_type": "game", "score": "0-0"},
        {"event_type": "2pt", "score": "2-0"},
        {"event_type": "period", "score": "75-74"},
        {"event_type": "game", "score": "75-74"},
        {"event_type": "assist", "score": "75-74"},
    ]

    trimmed = _trim_events_to_terminal_game(events, "75-74")

    assert len(trimmed) == 4
    assert trimmed[-1]["event_type"] == "game"


def test_bucketed_model_fits_and_predicts_probabilities() -> None:
    states = pd.DataFrame(
        [
            {
                "match_id": "1",
                "season": "2025-2026",
                "match_datetime": pd.Timestamp("2025-10-01"),
                "event_order": 1,
                "period_index": 4,
                "clock_seconds": 20,
                "seconds_remaining": 20,
                "score_diff": 6,
                "pre_game_prob": 0.6,
                "home_win": 1,
            },
            {
                "match_id": "2",
                "season": "2025-2026",
                "match_datetime": pd.Timestamp("2025-10-02"),
                "event_order": 1,
                "period_index": 4,
                "clock_seconds": 20,
                "seconds_remaining": 20,
                "score_diff": -6,
                "pre_game_prob": 0.4,
                "home_win": 0,
            },
            {
                "match_id": "3",
                "season": "2025-2026",
                "match_datetime": pd.Timestamp("2025-10-03"),
                "event_order": 1,
                "period_index": 2,
                "clock_seconds": 300,
                "seconds_remaining": 900,
                "score_diff": 10,
                "pre_game_prob": 0.7,
                "home_win": 1,
            },
            {
                "match_id": "4",
                "season": "2025-2026",
                "match_datetime": pd.Timestamp("2025-10-04"),
                "event_order": 1,
                "period_index": 2,
                "clock_seconds": 300,
                "seconds_remaining": 900,
                "score_diff": -10,
                "pre_game_prob": 0.3,
                "home_win": 0,
            },
        ]
    )

    model = fit_bucketed_model(states, min_bucket_samples=1)
    probabilities = predict_state_probabilities(states, model)

    assert len(probabilities) == len(states)
    assert all(0.0 < probability < 1.0 for probability in probabilities)
