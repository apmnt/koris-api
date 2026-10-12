import pandas as pd

from koris_api import SeasonData
from koris_modeling import to_frames


def test_tables_preserve_official_totals_and_linkage(dataset: SeasonData) -> None:
    before = dataset.games[0].SerializeToString()
    tables = to_frames([dataset])
    assert len(tables.games) == 1 and tables.games.loc[0, "boxscore_valid"]
    home = tables.teams.loc[tables.teams["home"]].iloc[0]
    assert home["rebounds"] == 46
    assert tables.players.loc[tables.players["home"], "rebounds"].sum() == 42
    assert len(tables.events) == 588 and len(tables.shots) == 171
    assert set(tables.shots["game_key"]) == set(tables.games["game_key"])
    assert tables.shots["player_id"].notna().all()
    assert dataset.games[0].SerializeToString() == before


def test_tables_keep_missing_distinct_from_zero_and_partial_box_invalid(
    dataset: SeasonData,
) -> None:
    game = dataset.games[0]
    game.home.players[0].stats.ClearField("blocks")
    game.home.players[1].stats.blocks = 0
    game.home.players[0].stats.points += 1
    tables = to_frames([dataset])
    assert pd.isna(tables.players.loc[0, "blocks"])
    assert tables.players.loc[1, "blocks"] == 0
    assert not tables.games.loc[0, "boxscore_valid"]


def test_empty_tables_have_stable_columns() -> None:
    tables = to_frames([])
    assert tables.games.empty and "season" in tables.games
    assert tables.shots.empty and "made" in tables.shots


def test_blank_zero_point_cells_do_not_discard_official_team_totals(
    dataset: SeasonData,
) -> None:
    player = next(p for p in dataset.games[0].home.players if p.stats.points == 0)
    player.stats.ClearField("points")
    tables = to_frames([dataset])
    assert tables.games.loc[0, "boxscore_valid"]
    assert tables.players["points"].isna().any()


def test_provider_comes_from_external_id_even_when_game_id_is_basketfi(
    dataset: SeasonData,
) -> None:
    dataset.games[0].id = "basketfi:match:123"
    assert to_frames([dataset]).games.loc[0, "provider"] == "genius"
