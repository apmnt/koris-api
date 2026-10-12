from dataclasses import replace

import pandas as pd
import pytest

from koris_api import SeasonData
from koris_modeling import to_frames
from koris_modeling.trends import (
    court_lines,
    court_shot_bins,
    scored_games,
    season_trends,
)


def test_season_trends_use_team_game_rates_and_pooled_shooting(
    dataset: SeasonData,
) -> None:
    tables = to_frames([dataset])
    game = tables.games.iloc[0]
    key = str(game.game_key)
    second = tables.games.copy()
    second["game_key"] = "second"
    first_teams = tables.teams.copy()
    first_teams["two_pointers_made"] = [1, 1]
    first_teams["two_pointers_attempted"] = [1, 1]
    second_teams = first_teams.copy()
    second_teams["game_key"] = "second"
    second_teams["two_pointers_made"] = [0, 0]
    second_teams["two_pointers_attempted"] = [9, 9]
    second_teams.loc[second_teams.home.eq(False), "assists"] = pd.NA
    tables = replace(
        tables,
        games=pd.concat([tables.games, second], ignore_index=True),
        teams=pd.concat([first_teams, second_teams], ignore_index=True),
    )
    trends = season_trends(tables).set_index("metric")
    assert trends.loc["points", "value"] == (game.home_score + game.away_score) / 2
    assert trends.loc["total_points", "value"] == game.home_score + game.away_score
    assert trends.loc["two_pct", "value"] == pytest.approx(2 / 20)
    assert trends.loc["two_pct", "games"] == 2
    assert trends.loc["assists", "games"] == 1
    assert trends.loc["assists", "available_final_games"] == 2
    assert first_teams.game_key.eq(key).all()


def test_zero_attempts_and_nonfinal_games_are_not_fabricated(
    dataset: SeasonData,
) -> None:
    tables = to_frames([dataset])
    tables.teams["three_pointers_made"] = 0
    tables.teams["three_pointers_attempted"] = 0
    result = season_trends(tables).set_index("metric")
    assert pd.isna(result.loc["three_pct", "value"])
    tables.games["status"] = "GAME_STATUS_IN_PROGRESS"
    assert season_trends(tables).empty


def test_court_bins_exclude_unknowns_free_throws_and_unverified_coordinates() -> None:
    shots = pd.DataFrame(
        [
            {
                "game_key": str(i),
                "category_id": "4",
                "league": "Korisliiga",
                "season": "2025-2026",
                "provider": provider,
                "point_value": points,
                "made": made,
                "x": x,
                "y": y,
            }
            for i, (provider, points, made, x, y) in enumerate(
                [
                    ("sportradar", 2, True, 0, 100),
                    ("sportradar", 3, False, 0, 100),
                    ("sportradar", 2, True, 100, 0),
                    ("sportradar", 2, None, 0, 100),
                    ("sportradar", 1, True, 0, 100),
                    ("sportradar", 2, True, 101, 50),
                    ("sportradar", 2, True, float("inf"), 50),
                    ("sportradar", 2, True, None, 50),
                    ("genius", 2, True, 0, 100),
                ]
            )
        ]
    )
    result = court_shot_bins(shots)
    season = result.loc[result.season.eq("2025-2026")]
    assert season.attempts.sum() == 3
    first = season.loc[season.x0.eq(0)].iloc[0]
    assert first.y0 == 0
    assert first.shooting_pct == 0.5
    edge = season.loc[season.x0.eq(27)].iloc[0]
    assert (edge.x1, edge.y0, edge.y1) == (28, 14, 15)
    assert result.loc[result.season.eq("All selected seasons"), "attempts"].sum() == 3
    with pytest.raises(ValueError):
        court_shot_bins(shots, bin_size=0)


def test_court_geometry_is_fixed_even_without_locations() -> None:
    lines = court_lines()
    assert (lines.x.min(), lines.x.max(), lines.y.min(), lines.y.max()) == (
        0,
        28,
        0,
        15,
    )
    empty = pd.DataFrame(
        columns=[
            "provider",
            "point_value",
            "x",
            "y",
            "made",
            "game_key",
            "category_id",
            "league",
            "season",
        ]
    )
    assert court_shot_bins(empty).empty


def test_validated_archive_scores_are_usable_without_inventing_status(
    dataset: SeasonData,
) -> None:
    tables = to_frames([dataset])
    tables.games["status"] = "GAME_STATUS_UNSPECIFIED"
    assert scored_games(tables).empty
    tables.games["provider"] = "baskethotel"
    assert len(scored_games(tables)) == 1
    assert tables.games.status.eq("GAME_STATUS_UNSPECIFIED").all()
    tables.games["boxscore_valid"] = False
    assert scored_games(tables).empty
