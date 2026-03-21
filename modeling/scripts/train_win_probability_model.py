from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from koris_api.win_probability import (
    accuracy_score,
    attach_playbyplay_cache,
    brier_score,
    build_match_results_frame,
    build_model_artifact,
    build_state_frame,
    calibration_table,
    compute_elo_probabilities,
    fetch_missing_playbyplay,
    fit_bucketed_model,
    load_matches,
    load_playbyplay_cache,
    log_loss,
    predict_global_probabilities,
    predict_state_probabilities,
    tune_elo_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train a Yale-style in-game win-probability model from koris-api "
            "play-by-play data."
        )
    )
    parser.add_argument(
        "--season-file",
        action="append",
        required=True,
        help="Path to a season JSON file. Pass once per season.",
    )
    parser.add_argument(
        "--train-season",
        action="append",
        required=True,
        help="Season label to use for model fitting, e.g. 2024-2025.",
    )
    parser.add_argument(
        "--validation-season",
        action="append",
        required=True,
        help="Season label to use for held-out validation, e.g. 2025-2026.",
    )
    parser.add_argument(
        "--output-dir",
        default="modeling/output/win_probability",
        help="Directory for coefficients, metrics, and validation tables.",
    )
    parser.add_argument(
        "--pbp-cache",
        help="Optional JSON cache for fetched play-by-play payloads.",
    )
    parser.add_argument(
        "--fetch-missing-pbp",
        action="store_true",
        help="Fetch missing play-by-play into the cache before training.",
    )
    parser.add_argument(
        "--competition-id",
        default="42145",
        help="Genius competition identifier used when fetching missing play-by-play.",
    )
    parser.add_argument(
        "--max-fetch-workers",
        type=int,
        default=8,
        help="Concurrent workers for play-by-play backfill.",
    )
    return parser.parse_args()


def metric_summary(actual: list[float], predicted: list[float]) -> dict[str, float]:
    return {
        "log_loss": log_loss(actual, predicted),
        "brier_score": brier_score(actual, predicted),
        "accuracy": accuracy_score(actual, predicted),
    }


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pbp_cache_path = (
        Path(args.pbp_cache) if args.pbp_cache else output_dir / "playbyplay_cache.json"
    )

    matches = load_matches(args.season_file)

    if args.fetch_missing_pbp:
        playbyplay_by_match_id, playbyplay_errors = fetch_missing_playbyplay(
            matches=matches,
            cache_path=pbp_cache_path,
            competition_id=args.competition_id,
            max_workers=args.max_fetch_workers,
        )
    else:
        playbyplay_by_match_id, playbyplay_errors = load_playbyplay_cache(
            pbp_cache_path
        )

    matches = attach_playbyplay_cache(matches, playbyplay_by_match_id)
    results = build_match_results_frame(matches)

    train_results = results[results["season"].isin(args.train_season)].copy()
    validation_results = results[results["season"].isin(args.validation_season)].copy()
    if train_results.empty:
        raise ValueError("No training matches found for the requested train seasons.")
    if validation_results.empty:
        raise ValueError(
            "No validation matches found for the requested validation seasons."
        )

    elo_config = tune_elo_config(train_results)
    scored_results = compute_elo_probabilities(results, elo_config)
    pregame_home_wp_by_match_id = dict(
        zip(scored_results["match_id"], scored_results["pregame_home_wp"])
    )

    states = build_state_frame(matches, pregame_home_wp_by_match_id)
    if states.empty:
        raise ValueError("No play-by-play states were available after hydration.")

    train_states = states[states["season"].isin(args.train_season)].copy()
    validation_states = states[states["season"].isin(args.validation_season)].copy()
    if train_states.empty:
        raise ValueError("No training state rows were available.")
    if validation_states.empty:
        raise ValueError("No validation state rows were available.")

    fitted_model = fit_bucketed_model(train_states)
    model = build_model_artifact(
        fitted_model=fitted_model,
        elo_config=elo_config,
        train_seasons=args.train_season,
        validation_seasons=args.validation_season,
    )

    train_actual = train_states["home_win"].astype(float).tolist()
    validation_actual = validation_states["home_win"].astype(float).tolist()

    train_pregame = train_states["pre_game_prob"].astype(float).tolist()
    validation_pregame = validation_states["pre_game_prob"].astype(float).tolist()

    train_global = predict_global_probabilities(
        train_states, model.global_coefficients
    ).tolist()
    validation_global = predict_global_probabilities(
        validation_states,
        model.global_coefficients,
    ).tolist()

    train_bucketed = predict_state_probabilities(train_states, model).tolist()
    validation_bucketed = predict_state_probabilities(validation_states, model).tolist()

    metrics = {
        "coverage": {
            "matches_total": int(len(results)),
            "train_matches": int(len(train_results)),
            "validation_matches": int(len(validation_results)),
            "matches_with_playbyplay_after_cache": int(
                sum(1 for match in matches if isinstance(match.get("playbyplay"), dict))
            ),
            "train_state_rows": int(len(train_states)),
            "validation_state_rows": int(len(validation_states)),
            "playbyplay_cache_entries": int(len(playbyplay_by_match_id)),
            "playbyplay_fetch_errors": int(len(playbyplay_errors)),
        },
        "elo_config": asdict(elo_config),
        "train": {
            "pregame_prior": metric_summary(train_actual, train_pregame),
            "single_logit": metric_summary(train_actual, train_global),
            "bucketed_model": metric_summary(train_actual, train_bucketed),
        },
        "validation": {
            "pregame_prior": metric_summary(validation_actual, validation_pregame),
            "single_logit": metric_summary(validation_actual, validation_global),
            "bucketed_model": metric_summary(validation_actual, validation_bucketed),
        },
    }

    validation_calibration = calibration_table(validation_actual, validation_bucketed)

    (output_dir / "model_coefficients.json").write_text(
        json.dumps(model.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    validation_calibration.to_csv(
        output_dir / "validation_calibration.csv", index=False
    )
    train_states.to_parquet(output_dir / "train_states.parquet", index=False)
    validation_states.to_parquet(output_dir / "validation_states.parquet", index=False)

    print("Win probability model trained.")
    print(f"Training seasons: {', '.join(args.train_season)}")
    print(f"Validation seasons: {', '.join(args.validation_season)}")
    print(f"Model coefficients: {output_dir / 'model_coefficients.json'}")
    print(f"Metrics: {output_dir / 'metrics.json'}")
    print(
        "Validation log loss "
        f"(pregame/global/bucketed): "
        f"{metrics['validation']['pregame_prior']['log_loss']:.4f} / "
        f"{metrics['validation']['single_logit']['log_loss']:.4f} / "
        f"{metrics['validation']['bucketed_model']['log_loss']:.4f}"
    )


if __name__ == "__main__":
    main()
