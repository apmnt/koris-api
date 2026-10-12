from dataclasses import replace

import pandas as pd

from koris_api import PlayerProfile, PlayerProfiles, Season
from koris_modeling.player_profiles import attach_profiles, profile_table


def profiles() -> PlayerProfiles:
    return PlayerProfiles(
        Season("4", "huki2526", "2025-2026", "Korisliiga"),
        (PlayerProfile("12", "22", "Kobrat", "Test Player", "0", 2001, 195, None),),
        (),
        1,
        "2026-10-12T00:00:00Z",
    )


def test_profile_join_is_scoped_and_never_multiplies_games() -> None:
    players = pd.DataFrame(
        {
            "category_id": ["4"] * 5,
            "season": ["2025-2026"] * 4 + ["2024-2025"],
            "team": ["Kobrat"] * 3 + ["Other", "Kobrat"],
            "player": [
                " test  PLAYER ",
                "Test Player",
                "T. Player",
                "Test Player",
                "Test Player",
            ],
            "jersey_number": ["0", "99", "0", "0", "0"],
        }
    )
    result = attach_profiles(players, profile_table([profiles()]))
    assert len(result) == 5
    assert result.profile_matched.tolist() == [True, False, False, False, False]
    assert result.birth_year.iloc[0] == 2001
    assert result.birth_year.iloc[1:].isna().all()
    assert result.weight_kg.isna().all()
    assert result.basketfi_player_id.iloc[0] == "12"
    assert "birthdate" not in result.columns


def test_ambiguous_and_empty_profiles_stay_unmatched() -> None:
    dataset = profiles()
    frame = profile_table([dataset])
    players = frame.rename(columns={"name": "player"}).drop(
        columns=["birth_year", "height_cm", "weight_kg", "fetched_at", "player_id"]
    )
    ambiguous = replace(
        dataset, players=(*dataset.players, replace(dataset.players[0], player_id="99"))
    )
    assert not attach_profiles(
        players, profile_table([ambiguous])
    ).profile_matched.any()
    assert not attach_profiles(players, profile_table([])).profile_matched.any()
    assert "birth_year" in profile_table([]).columns
