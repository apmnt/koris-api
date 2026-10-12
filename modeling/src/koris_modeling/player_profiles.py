"""Pure projections and conservative team/season profile joins for analysis."""

from collections.abc import Sequence
from dataclasses import asdict, fields

import pandas as pd

from koris_api import PlayerProfile, PlayerProfiles

PROFILE_FIELDS = ("birth_year", "height_cm", "weight_kg")
CONTEXT = ("category_id", "league", "season")


def profile_table(datasets: Sequence[PlayerProfiles]) -> pd.DataFrame:
    """One row per Basket.fi player/team/season roster observation."""
    rows = [
        {
            "category_id": dataset.season.category_id,
            "league": dataset.season.league,
            "season": dataset.season.name,
            **asdict(player),
            "fetched_at": dataset.fetched_at,
        }
        for dataset in datasets
        for player in dataset.players
    ]
    columns = [*CONTEXT, *(field.name for field in fields(PlayerProfile)), "fetched_at"]
    return pd.DataFrame(rows, columns=columns).convert_dtypes()


def attach_profiles(players: pd.DataFrame, profiles: pd.DataFrame) -> pd.DataFrame:
    """Match unique full name + jersey within a reported team and season.

    No fuzzy/initial-only or cross-season joins. Ambiguous roster keys remain
    unmatched. The returned Basket.fi ID is evidence of this descriptive join,
    not a claim that provider career identities have been resolved.
    """
    left = players.copy()
    right = profiles.copy()
    keys = ["category_id", "season", "_team", "_name", "_jersey"]
    for frame, name_column in ((left, "player"), (right, "name")):
        for output, source in (
            ("_team", "team"),
            ("_name", name_column),
            ("_jersey", "jersey_number"),
        ):
            frame[output] = (
                frame[source]
                .astype("string")
                .str.strip()
                .str.casefold()
                .str.replace(r"\s+", " ", regex=True)
            )
    eligible = right[keys].notna().all(axis=1) & ~right[keys].isin([""]).any(axis=1)
    right = right.loc[eligible & ~right.duplicated(keys, keep=False)]
    right = right.rename(
        columns={"player_id": "basketfi_player_id", "fetched_at": "profile_fetched_at"}
    )
    columns = ["basketfi_player_id", *PROFILE_FIELDS, "profile_fetched_at"]
    merged = left.merge(
        right[[*keys, *columns]],
        how="left",
        on=keys,
        validate="many_to_one",
        sort=False,
    )
    merged["profile_matched"] = merged.basketfi_player_id.notna()
    return merged.drop(columns=["_team", "_name", "_jersey"]).convert_dtypes()
