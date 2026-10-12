"""Pure season summaries and spatial shooting percentages; no source I/O."""

import math

import numpy as np
import pandas as pd

from .tables import AnalysisTables

KEYS = ["category_id", "league", "season"]
COUNT_METRICS = {
    "rebounds": "Rebounds",
    "offensive_rebounds": "Offensive rebounds",
    "defensive_rebounds": "Defensive rebounds",
    "assists": "Assists",
    "steals": "Steals",
    "blocks": "Blocks",
    "turnovers": "Turnovers",
    "personal_fouls": "Personal fouls",
    "three_pointers_attempted": "3-point attempts",
    "free_throws_attempted": "Free-throw attempts",
}


def scored_games(tables: AnalysisTables) -> pd.DataFrame:
    """Final results plus validated scored BasketHotel archive games.

    Archive snapshots have no canonical status flag. Accept their known scores
    only with a validated complete box score; never accept an explicit live or
    scheduled status, or an unknown status from another provider.
    """
    games = tables.games
    archived = (
        games.provider.eq("baskethotel")
        & games.status.eq("GAME_STATUS_UNSPECIFIED")
        & games.boxscore_valid.fillna(False)
    )
    return games.loc[
        (games.status.eq("GAME_STATUS_FINAL") | archived)
        & games.home_score.notna()
        & games.away_score.notna()
    ].copy()


def season_trends(tables: AnalysisTables) -> pd.DataFrame:
    """One observation per season/metric, with the actual eligible game count.

    Counting stats are per TEAM per game; total points is explicitly both teams.
    Use scored_games' completed results. A metric requires both teams' relevant fields,
    avoiding one-sided game samples. Shooting percentages pool makes/attempts.
    """
    final = scored_games(tables)
    boxes = tables.teams.loc[
        tables.teams.game_key.isin(final.game_key)
        & tables.teams.boxscore_valid.fillna(False)
    ].copy()
    counts = final.groupby(KEYS).game_key.nunique().to_dict()
    rows: list[dict[str, object]] = []

    def append(frame: pd.DataFrame, metric: str, label: str, unit: str) -> None:
        for key, sample in frame.groupby(KEYS):
            denominator = sample["denominator"].sum()
            value = sample["numerator"].sum() / denominator if denominator > 0 else None
            rows.append(
                dict(
                    zip(KEYS, key),
                    metric=metric,
                    label=label,
                    unit=unit,
                    value=float(value)
                    if value is not None and pd.notna(value)
                    else None,
                    games=sample.game_key.nunique(),
                    available_final_games=int(counts[key]),
                )
            )

    final["numerator"] = final.home_score + final.away_score
    final["denominator"] = 1
    append(final, "total_points", "Total points (both teams)", "per game")
    final["denominator"] = 2
    append(final, "points", "Points", "per team per game")

    def paired(fields: list[str]) -> pd.DataFrame:
        known = boxes.dropna(subset=fields).copy()
        return known.loc[
            known.groupby("game_key").game_key.transform("size").eq(2)
        ].copy()

    for field, label in COUNT_METRICS.items():
        sample = paired([field])
        sample["numerator"] = sample[field]
        sample["denominator"] = 1
        append(sample, field, label, "per team per game")

    two = "two_pointers_attempted"
    three = "three_pointers_attempted"
    tm = "two_pointers_made"
    thm = "three_pointers_made"
    ft = "free_throws_attempted"
    sample = paired([two, three])
    sample["numerator"] = sample[two] + sample[three]
    sample["denominator"] = 1
    append(sample, "field_goal_attempts", "Field-goal attempts", "per team per game")
    sample = paired([two, three, ft, "offensive_rebounds", "turnovers"])
    sample["numerator"] = (
        sample[two]
        + sample[three]
        - sample.offensive_rebounds
        + sample.turnovers
        + 0.44 * sample[ft]
    )
    sample["denominator"] = 1
    append(sample, "possessions", "Estimated possessions", "per team per game")

    ratio_fields = {
        "two_pct": ("2-point %", [tm, two]),
        "three_pct": ("3-point %", [thm, three]),
        "free_throw_pct": ("Free-throw %", ["free_throws_made", ft]),
        "field_goal_pct": ("Field-goal %", [tm, thm, two, three]),
        "efg": ("Effective field-goal %", [tm, thm, two, three]),
        "three_attempt_share": ("3-point attempt share", [three, two]),
    }
    for metric, (label, fields) in ratio_fields.items():
        sample = paired(fields)
        if metric in ("field_goal_pct", "efg"):
            sample["numerator"] = (
                sample[tm] + (1.5 if metric == "efg" else 1) * sample[thm]
            )
            sample["denominator"] = sample[two] + sample[three]
        elif metric == "three_attempt_share":
            sample["numerator"] = sample[three]
            sample["denominator"] = sample[two] + sample[three]
        else:
            sample["numerator"] = sample[fields[0]]
            sample["denominator"] = sample[fields[1]]
        # Keep zero-attempt games in the sample count, but never report 0/0 as 0%.
        sample["denominator"] = sample.denominator.astype(float)
        append(sample, metric, label, "ratio")

    return pd.DataFrame(
        rows,
        columns=[
            *KEYS,
            "metric",
            "label",
            "unit",
            "value",
            "games",
            "available_final_games",
        ],
    )


def court_shot_bins(shots: pd.DataFrame, *, bin_size: float = 1.0) -> pd.DataFrame:
    """Sportradar DataCore 0–100 coordinates to a 28 × 15 m full court.

    https://docs.sportradar.com/datacore/sports-apis/basketball/datacore-api-v1/match-play-by-play
    x is left-to-right, y top-to-bottom. Physical y below is bottom-to-top.
    Preserve both baskets; do not infer attacking direction or fold long shots.
    Unknown outcomes, free throws, nonfinite/out-of-court points, and unverified
    provider systems are excluded. Empty cells stay absent, never 0%.
    """
    if not math.isfinite(bin_size) or bin_size <= 0:
        raise ValueError("bin_size must be finite and positive")
    sample = (
        shots.loc[shots.provider.eq("sportradar") & shots.point_value.isin([2, 3])]
        .dropna(subset=["x", "y", "made"])
        .copy()
    )
    sample = sample.loc[sample.x.between(0, 100) & sample.y.between(0, 100)].copy()
    nx, ny = math.ceil(28 / bin_size), math.ceil(15 / bin_size)
    sample["ix"] = np.minimum(
        np.floor(sample.x.astype(float) * 0.28 / bin_size), nx - 1
    )
    sample["iy"] = np.minimum(
        np.floor((100 - sample.y.astype(float)) * 0.15 / bin_size), ny - 1
    )
    sample["made"] = sample.made.astype(int)
    all_seasons = sample.assign(season="All selected seasons")
    binned = (
        pd.concat([sample, all_seasons], ignore_index=True)
        .groupby([*KEYS, "provider", "ix", "iy"], dropna=False)
        .agg(
            attempts=("made", "size"),
            makes=("made", "sum"),
            games=("game_key", "nunique"),
        )
        .reset_index()
    )
    binned["x0"] = binned.ix * bin_size
    binned["x1"] = np.minimum(binned.x0 + bin_size, 28)
    binned["y0"] = binned.iy * bin_size
    binned["y1"] = np.minimum(binned.y0 + bin_size, 15)
    binned["shooting_pct"] = binned.makes / binned.attempts
    return binned.drop(columns=["ix", "iy"])


def court_lines() -> pd.DataFrame:
    """Schematic FIBA court in metres; independent of observed shot extents.

    Geometry: https://assets.fiba.basketball/image/upload/documents-corporate-fiba-official-rules-2024-v10a.pdf
    """
    rows: list[dict[str, object]] = []

    def path(name: str, points: list[tuple[float, float]]) -> None:
        rows.extend(
            {"path": name, "order": i, "x": x, "y": y}
            for i, (x, y) in enumerate(points)
        )

    path("boundary", [(0, 0), (28, 0), (28, 15), (0, 15), (0, 0)])
    path("centre", [(14, 0), (14, 15)])
    theta = np.linspace(0, 2 * math.pi, 120)
    path(
        "centre circle",
        [(14 + 1.8 * math.cos(t), 7.5 + 1.8 * math.sin(t)) for t in theta],
    )
    for side in (0, 1):

        def reflect(x: float, side: int = side) -> float:
            return x if side == 0 else 28 - x

        path(
            f"key{side}",
            [
                (reflect(x), y)
                for x, y in [(0, 5.05), (5.8, 5.05), (5.8, 9.95), (0, 9.95)]
            ],
        )
        path(f"backboard{side}", [(reflect(1.2), 6.6), (reflect(1.2), 8.4)])
        path(
            f"hoop{side}",
            [
                (reflect(1.575 + 0.225 * math.cos(t)), 7.5 + 0.225 * math.sin(t))
                for t in theta
            ],
        )
        path(
            f"free throw{side}",
            [
                (reflect(5.8 + 1.8 * math.cos(t)), 7.5 + 1.8 * math.sin(t))
                for t in theta
            ],
        )
        angle = math.asin(6.6 / 6.75)
        arc = [
            (reflect(1.575 + 6.75 * math.cos(t)), 7.5 + 6.75 * math.sin(t))
            for t in np.linspace(-angle, angle, 120)
        ]
        path(f"three point{side}", [(reflect(0), 0.9), *arc, (reflect(0), 14.1)])
    return pd.DataFrame(rows)
