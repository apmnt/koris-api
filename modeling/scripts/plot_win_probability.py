from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from koris_api.win_probability import (
    BucketedWinProbabilityModel,
    EloConfig,
    WinProbabilityModelInput,
    WinProbabilityPipelineData,
    available_win_probability_models,
    attach_playbyplay_cache,
    build_match_results_frame,
    build_state_frame,
    compute_elo_probabilities,
    load_matches,
    load_playbyplay_cache,
    predict_with_win_probability_model,
)

HOME_COLOR = "#1D4ED8"
AWAY_COLOR = "#D97706"
MODEL_MODE_CHOICES = available_win_probability_models()


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
        choices=MODEL_MODE_CHOICES,
        default="hybrid",
        help="Prediction mode for the plotted curve.",
    )
    parser.add_argument(
        "--compare-modes",
        nargs="+",
        choices=MODEL_MODE_CHOICES,
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
        "--point-diff-scale",
        type=float,
        default=8.0,
        help="Default logistic scale for normalized score differential.",
    )
    parser.add_argument(
        "--fit-point-diff-scale",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Fit the normalized score-diff logistic scale per plot panel to best match "
            "the predicted win probability."
        ),
    )
    parser.add_argument(
        "--point-diff-scale-min",
        type=float,
        default=1.0,
        help="Minimum scale considered when fitting normalized score differential.",
    )
    parser.add_argument(
        "--point-diff-scale-max",
        type=float,
        default=40.0,
        help="Maximum scale considered when fitting normalized score differential.",
    )
    parser.add_argument(
        "--point-diff-scale-step",
        type=float,
        default=0.25,
        help="Grid-search step size for score-differential normalization fitting.",
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


def build_pipeline_data(
    *,
    matches: list[dict[str, Any]],
    model: BucketedWinProbabilityModel,
    season_filter: list[str] | None,
) -> WinProbabilityPipelineData:
    results = build_match_results_frame(matches)
    scored_results = compute_elo_probabilities(results, model.elo_config)
    pregame_home_wp_by_match_id = dict(
        zip(scored_results["match_id"], scored_results["pregame_home_wp"])
    )
    states = build_state_frame(matches, pregame_home_wp_by_match_id)
    if season_filter:
        states = states[states["season"].isin(season_filter)].copy()
    return WinProbabilityPipelineData(
        matches=matches,
        match_results=results,
        scored_results=scored_results,
        states=states,
    )


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
    pipeline_data: WinProbabilityPipelineData,
    model: BucketedWinProbabilityModel,
    mode: str,
    hybrid_cutoff_seconds: int,
) -> np.ndarray:
    model_input = WinProbabilityModelInput(
        pipeline=pipeline_data,
        artifact=model,
        options={"hybrid_cutoff_seconds": hybrid_cutoff_seconds},
    )
    return predict_with_win_probability_model(mode, model_input)


def append_model_probabilities(
    pipeline_data: WinProbabilityPipelineData,
    model: BucketedWinProbabilityModel,
    modes: list[str],
    hybrid_cutoff_seconds: int,
) -> pd.DataFrame:
    enriched_states = pipeline_data.states.copy()
    for mode in sorted(set(modes)):
        mode_pipeline_data = WinProbabilityPipelineData(
            matches=pipeline_data.matches,
            match_results=pipeline_data.match_results,
            scored_results=pipeline_data.scored_results,
            states=enriched_states,
        )
        enriched_states[f"home_win_probability_{mode}"] = build_predictions(
            pipeline_data=mode_pipeline_data,
            model=model,
            mode=mode,
            hybrid_cutoff_seconds=hybrid_cutoff_seconds,
        )
    return enriched_states


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
    values: np.ndarray,
    total_seconds: int,
    window_seconds: int,
) -> tuple[np.ndarray, np.ndarray]:
    series_frame = frame.copy()
    series_frame["series_value"] = values
    event_series = (
        series_frame.sort_values(by=["elapsed_seconds", "event_order"], kind="stable")
        .groupby("elapsed_seconds", sort=True)["series_value"]
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


def normalize_score_diff_probability(
    score_diff: np.ndarray,
    scale: float,
) -> np.ndarray:
    bounded_scale = max(float(scale), 1e-6)
    logits = np.clip(score_diff / bounded_scale, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-logits))


def fit_point_diff_scale(
    score_diff: np.ndarray,
    target_home_win_probability: np.ndarray,
    *,
    default_scale: float,
    scale_min: float,
    scale_max: float,
    scale_step: float,
    fit_enabled: bool,
) -> tuple[float, np.ndarray]:
    if not fit_enabled:
        return default_scale, normalize_score_diff_probability(score_diff, default_scale)

    lo = max(1e-6, min(scale_min, scale_max))
    hi = max(scale_min, scale_max)
    step = max(scale_step, 1e-6)
    candidates = np.arange(lo, hi + step * 0.5, step, dtype=float)
    if candidates.size == 0:
        candidates = np.array([default_scale], dtype=float)

    best_scale = float(default_scale)
    best_curve = normalize_score_diff_probability(score_diff, best_scale)
    best_mse = float(np.mean(np.square(best_curve - target_home_win_probability)))
    for scale in candidates:
        candidate_curve = normalize_score_diff_probability(score_diff, float(scale))
        mse = float(np.mean(np.square(candidate_curve - target_home_win_probability)))
        if mse < best_mse:
            best_mse = mse
            best_scale = float(scale)
            best_curve = candidate_curve
    return best_scale, best_curve


def probability_error_metrics(
    predicted: np.ndarray, baseline: np.ndarray
) -> tuple[float, float, float]:
    diff = predicted - baseline
    mae = float(np.mean(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(np.square(diff))))
    max_abs = float(np.max(np.abs(diff)))
    return mae, rmse, max_abs


def plot_mode_panel(
    ax: plt.Axes,
    frame: pd.DataFrame,
    match: dict[str, object],
    probability_column: str,
    mode_label: str,
    smooth_window_seconds: int,
    point_diff_scale: float,
    fit_point_diff_scale_enabled: bool,
    point_diff_scale_min: float,
    point_diff_scale_max: float,
    point_diff_scale_step: float,
) -> None:
    total_seconds = int(frame["elapsed_seconds"].max())
    home_win_probability = frame[probability_column].to_numpy(dtype=float)
    score_diff = frame["score_diff"].to_numpy(dtype=float)
    fitted_scale, normalized_point_diff_probability = fit_point_diff_scale(
        score_diff=score_diff,
        target_home_win_probability=home_win_probability,
        default_scale=point_diff_scale,
        scale_min=point_diff_scale_min,
        scale_max=point_diff_scale_max,
        scale_step=point_diff_scale_step,
        fit_enabled=fit_point_diff_scale_enabled,
    )
    mae, rmse, max_abs = probability_error_metrics(
        home_win_probability,
        normalized_point_diff_probability,
    )
    smooth_x, smooth_home_probability = smooth_step_series(
        frame=frame,
        values=home_win_probability,
        total_seconds=total_seconds,
        window_seconds=smooth_window_seconds,
    )
    _, smooth_normalized_point_diff_probability = smooth_step_series(
        frame=frame,
        values=normalized_point_diff_probability,
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
        label=f"{match['home_team']} prediction",
    )
    ax.plot(
        smooth_x,
        1.0 - smooth_home_probability,
        color=AWAY_COLOR,
        linewidth=2.7,
        label=f"{match['away_team']} prediction",
    )
    ax.plot(
        smooth_x,
        smooth_normalized_point_diff_probability,
        color="#7C3AED",
        linewidth=2.0,
        linestyle="--",
        label=f"{match['home_team']} normalized point diff",
    )
    ax.fill_between(
        smooth_x,
        smooth_home_probability,
        smooth_normalized_point_diff_probability,
        color="#7C3AED",
        alpha=0.1,
        linewidth=0.0,
        label="prediction gap",
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
            f"{min_winner_probability * 100:.1f}%\n"
            f"Point-diff scale: {fitted_scale:.2f}\n"
            f"Δ MAE: {mae * 100:.2f}pp  RMSE: {rmse * 100:.2f}pp\n"
            f"Δ Max: {max_abs * 100:.2f}pp"
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

    season_filter = args.season or model.validation_seasons
    pipeline_data = build_pipeline_data(
        matches=matches,
        model=model,
        season_filter=season_filter,
    )
    states = pipeline_data.states.copy()

    if states.empty:
        raise ValueError("No state rows available for plotting.")

    plot_modes = args.compare_modes or [args.mode]
    ranking_mode = "hybrid" if "hybrid" in plot_modes else plot_modes[0]
    prediction_modes = sorted(set(plot_modes + [ranking_mode]))
    pipeline_data = WinProbabilityPipelineData(
        matches=pipeline_data.matches,
        match_results=pipeline_data.match_results,
        scored_results=pipeline_data.scored_results,
        states=states,
    )
    states = append_model_probabilities(
        pipeline_data=pipeline_data,
        model=model,
        modes=prediction_modes,
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
                point_diff_scale=args.point_diff_scale,
                fit_point_diff_scale_enabled=args.fit_point_diff_scale,
                point_diff_scale_min=args.point_diff_scale_min,
                point_diff_scale_max=args.point_diff_scale_max,
                point_diff_scale_step=args.point_diff_scale_step,
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
