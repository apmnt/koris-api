from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from koris_api.win_probability import (
    BucketedWinProbabilityModel,
    EloConfig,
    attach_playbyplay_cache,
    build_match_results_frame,
    build_state_frame,
    compute_elo_probabilities,
    load_matches,
    load_playbyplay_cache,
    predict_global_probabilities,
    predict_state_probabilities,
)

HOME_COLOR = "#1D4ED8"
AWAY_COLOR = "#D97706"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot Yale-style win-probability charts from the trained model."
    )
    parser.add_argument(
        "--model-dir",
        default="modeling/output/win_probability_1div",
        help="Directory containing model_coefficients.json and metrics.json.",
    )
    parser.add_argument(
        "--season-file",
        action="append",
        required=True,
        help="Season JSON file. Pass once per season you want available for plotting.",
    )
    parser.add_argument(
        "--pbp-cache",
        help="Optional play-by-play cache JSON. Defaults to <model-dir>/playbyplay_cache.json.",
    )
    parser.add_argument(
        "--season",
        action="append",
        help="Season filter for candidate games. Defaults to the model validation season(s).",
    )
    parser.add_argument(
        "--match-id",
        action="append",
        help="Specific internal match_id to plot. Pass multiple times for multiple games.",
    )
    parser.add_argument(
        "--top-n-exciting",
        type=int,
        default=3,
        help="If no --match-id is given, plot the top N games by game excitement index.",
    )
    parser.add_argument(
        "--mode",
        choices=["bucketed", "global", "hybrid"],
        default="hybrid",
        help="Prediction mode for the plotted curve.",
    )
    parser.add_argument(
        "--compare-modes",
        nargs="+",
        choices=["bucketed", "global", "hybrid"],
        help="If provided, render these modes side by side in the same figure.",
    )
    parser.add_argument(
        "--hybrid-cutoff-seconds",
        type=int,
        default=300,
        help="For hybrid mode, use the bucketed model at or below this many seconds remaining.",
    )
    parser.add_argument(
        "--smooth-window-seconds",
        type=int,
        default=30,
        help="Centered rolling window for the bold smoothed overlay.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for PNG outputs. Defaults to <model-dir>/plots.",
    )
    return parser.parse_args()


def load_model(model_dir: Path) -> BucketedWinProbabilityModel:
    payload = json.loads(
        (model_dir / "model_coefficients.json").read_text(encoding="utf-8")
    )
    elo_config = EloConfig(**payload["elo_config"])
    fitted = BucketedWinProbabilityModel(
        bucket_starts=payload["bucket_starts"],
        coefficients=[
            [item["intercept"], item["score_diff"], item["pre_game_prob"]]
            for item in payload["coefficients"]
        ],
        global_coefficients=[
            payload["global_coefficients"]["intercept"],
            payload["global_coefficients"]["score_diff"],
            payload["global_coefficients"]["pre_game_prob"],
        ],
        elo_config=elo_config,
        train_seasons=payload["train_seasons"],
        validation_seasons=payload["validation_seasons"],
    )
    return fitted


def elapsed_seconds(period_index: int, clock_seconds: int) -> int:
    if period_index <= 4:
        return (period_index - 1) * 600 + (600 - clock_seconds)
    return 2400 + (period_index - 5) * 300 + (300 - clock_seconds)


def format_elapsed(seconds: float, _: int) -> str:
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}:{secs:02d}"


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def build_predictions(
    states: pd.DataFrame,
    model: BucketedWinProbabilityModel,
    mode: str,
    hybrid_cutoff_seconds: int,
) -> np.ndarray:
    global_predictions = predict_global_probabilities(states, model.global_coefficients)
    if mode == "global":
        return global_predictions

    bucketed_predictions = predict_state_probabilities(states, model)
    if mode == "bucketed":
        return bucketed_predictions

    hybrid_predictions = global_predictions.copy()
    mask = states["seconds_remaining"].to_numpy(dtype=int) <= hybrid_cutoff_seconds
    hybrid_predictions[mask] = bucketed_predictions[mask]
    return hybrid_predictions


def game_excitement_index(
    home_win_probability: np.ndarray, total_game_seconds: int
) -> float:
    if len(home_win_probability) <= 1 or total_game_seconds <= 0:
        return 0.0
    return float(
        np.sum(np.abs(np.diff(home_win_probability))) * 2400.0 / total_game_seconds
    )


def smooth_step_series(
    frame: pd.DataFrame,
    probability_column: str,
    total_seconds: int,
    window_seconds: int,
) -> tuple[np.ndarray, np.ndarray]:
    event_series = (
        frame.sort_values(by=["elapsed_seconds", "event_order"], kind="stable")
        .groupby("elapsed_seconds", sort=True)[probability_column]
        .last()
    )
    dense_index = pd.RangeIndex(0, total_seconds + 1)
    dense_series = event_series.reindex(dense_index).ffill().bfill()
    smoothed = dense_series.rolling(
        window=max(1, int(window_seconds)),
        center=True,
        min_periods=1,
    ).mean()
    return dense_index.to_numpy(dtype=float), smoothed.to_numpy(dtype=float)


def plot_mode_panel(
    ax: plt.Axes,
    frame: pd.DataFrame,
    match: dict[str, object],
    probability_column: str,
    mode_label: str,
    smooth_window_seconds: int,
) -> None:
    total_seconds = int(frame["elapsed_seconds"].max())
    home_win_probability = frame[probability_column].to_numpy(dtype=float)
    smooth_x, smooth_home_probability = smooth_step_series(
        frame=frame,
        probability_column=probability_column,
        total_seconds=total_seconds,
        window_seconds=smooth_window_seconds,
    )

    ax.step(
        frame["elapsed_seconds"],
        home_win_probability,
        where="post",
        color=HOME_COLOR,
        linewidth=1.6,
        alpha=0.22,
    )
    ax.step(
        frame["elapsed_seconds"],
        1.0 - home_win_probability,
        where="post",
        color=AWAY_COLOR,
        linewidth=1.6,
        alpha=0.22,
    )
    ax.plot(
        smooth_x,
        smooth_home_probability,
        color=HOME_COLOR,
        linewidth=2.7,
        label=str(match["home_team"]),
    )
    ax.plot(
        smooth_x,
        1.0 - smooth_home_probability,
        color=AWAY_COLOR,
        linewidth=2.7,
        label=str(match["away_team"]),
    )

    home_score = int(match["home_score"])
    away_score = int(match["away_score"])
    home_win = home_score > away_score
    winner_probability = (
        home_win_probability if home_win else 1.0 - home_win_probability
    )
    gei = game_excitement_index(home_win_probability, total_seconds)
    min_winner_probability = float(np.min(winner_probability))

    ax.axhline(0.5, linestyle="--", linewidth=1.0, color="#666666", alpha=0.7)
    ax.set_ylim(0.0, 1.0)
    ax.set_xlim(0, total_seconds)
    ax.set_xlabel("Time Elapsed")
    ax.xaxis.set_major_formatter(FuncFormatter(format_elapsed))
    ax.grid(axis="y", alpha=0.2)
    ax.set_title(mode_label.replace("_", " ").title(), fontsize=13)
    ax.text(
        0.015,
        0.05,
        (
            f"GEI: {gei:.2f}\n"
            f"Min {match['home_team'] if home_win else match['away_team']} WP: "
            f"{min_winner_probability * 100:.1f}%"
        ),
        transform=ax.transAxes,
        fontsize=9,
        bbox={
            "boxstyle": "round",
            "facecolor": "white",
            "alpha": 0.88,
            "edgecolor": "#dddddd",
        },
    )


def main() -> None:
    args = parse_args()

    model_dir = Path(args.model_dir)
    output_dir = Path(args.output_dir) if args.output_dir else model_dir / "plots"
    output_dir.mkdir(parents=True, exist_ok=True)

    model = load_model(model_dir)
    pbp_cache_path = (
        Path(args.pbp_cache) if args.pbp_cache else model_dir / "playbyplay_cache.json"
    )

    matches = load_matches(args.season_file)
    playbyplay_by_match_id, _ = load_playbyplay_cache(pbp_cache_path)
    matches = attach_playbyplay_cache(matches, playbyplay_by_match_id)

    results = build_match_results_frame(matches)
    scored_results = compute_elo_probabilities(results, model.elo_config)
    pregame_home_wp_by_match_id = dict(
        zip(scored_results["match_id"], scored_results["pregame_home_wp"])
    )

    states = build_state_frame(matches, pregame_home_wp_by_match_id)
    season_filter = args.season or model.validation_seasons
    if season_filter:
        states = states[states["season"].isin(season_filter)].copy()

    if states.empty:
        raise ValueError("No state rows available for plotting.")

    plot_modes = args.compare_modes or [args.mode]
    ranking_mode = "hybrid" if "hybrid" in plot_modes else plot_modes[0]
    prediction_modes = sorted(set(plot_modes + [ranking_mode]))
    for mode in prediction_modes:
        states[f"home_win_probability_{mode}"] = build_predictions(
            states=states,
            model=model,
            mode=mode,
            hybrid_cutoff_seconds=args.hybrid_cutoff_seconds,
        )
    states["elapsed_seconds"] = [
        elapsed_seconds(int(period_index), int(clock_seconds))
        for period_index, clock_seconds in zip(
            states["period_index"],
            states["clock_seconds"],
        )
    ]

    match_lookup = {str(match["match_id"]): match for match in matches}
    candidate_ids: list[str]
    if args.match_id:
        candidate_ids = [str(match_id) for match_id in args.match_id]
    else:
        summaries: list[tuple[str, float]] = []
        for match_id, frame in states.groupby("match_id", sort=False):
            frame = frame.sort_values(
                by=["elapsed_seconds", "event_order"], kind="stable"
            )
            total_seconds = int(frame["elapsed_seconds"].max())
            gei = game_excitement_index(
                frame[f"home_win_probability_{ranking_mode}"].to_numpy(dtype=float),
                total_seconds,
            )
            summaries.append((str(match_id), gei))
        summaries.sort(key=lambda item: item[1], reverse=True)
        candidate_ids = [match_id for match_id, _ in summaries[: args.top_n_exciting]]

    for match_id in candidate_ids:
        frame = states[states["match_id"] == match_id].copy()
        if frame.empty:
            continue
        frame = frame.sort_values(by=["elapsed_seconds", "event_order"], kind="stable")

        match = match_lookup.get(str(match_id))
        if match is None:
            continue

        home_score = int(match["home_score"])
        away_score = int(match["away_score"])
        if len(plot_modes) == 1:
            fig, axes = plt.subplots(1, 1, figsize=(12, 7))
            axes_list = [axes]
        else:
            fig, axes = plt.subplots(
                1, len(plot_modes), figsize=(7.5 * len(plot_modes), 7), sharey=True
            )
            axes_list = list(np.atleast_1d(axes))

        title = f"{match['away_team']} at {match['home_team']}"
        subtitle = (
            f"{match['date']}  |  Final {match['home_team']} {home_score}, "
            f"{match['away_team']} {away_score}  |  Smooth overlay: {args.smooth_window_seconds}s"
        )
        fig.suptitle(title, fontsize=17, y=0.98)
        fig.text(0.5, 0.94, subtitle, ha="center", fontsize=12)

        for axis, mode in zip(axes_list, plot_modes):
            plot_mode_panel(
                ax=axis,
                frame=frame,
                match=match,
                probability_column=f"home_win_probability_{mode}",
                mode_label=mode,
                smooth_window_seconds=args.smooth_window_seconds,
            )
        axes_list[0].set_ylabel("Win Probability")
        axes_list[0].legend(loc="upper left")

        file_name = sanitize_filename(
            f"{match['date']}_{match_id}_{match['away_team']}_at_{match['home_team']}_{'_vs_'.join(plot_modes)}.png"
        )
        output_path = output_dir / file_name
        fig.tight_layout(rect=[0, 0, 1, 0.92])
        fig.savefig(output_path, dpi=180)
        plt.close(fig)
        print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
