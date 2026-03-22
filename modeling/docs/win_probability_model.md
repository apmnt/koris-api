# Win Probability Model

This repo now includes a Yale-style in-game win probability pipeline built on top of `koris-api` play-by-play.

## Method

The model follows the same core structure as the Yale NCAA model:

- Estimate a **pregame prior** for the home team.
- Convert each play-by-play event into a game state with:
  - `seconds_remaining`
  - `score_diff` (`home_score - away_score`)
  - `pre_game_prob`
- Fit a separate regularized logistic regression for each remaining-time bucket using the Yale bucket schedule:
  - `0..29` seconds by 1 second
  - `30..60` seconds by 2 seconds
  - `70..2400` seconds by 10 seconds

## Difference From Yale

Yale uses the Vegas point spread as the pregame prior. `koris-api` data does not include betting lines, so this implementation replaces that input with a **chronological Elo-based pregame win probability** learned from prior match results.

## Pluggable prediction interface

The win-probability pipeline now supports model switching through a shared interface:

- `WinProbabilityPipelineData`: full pipeline payload (`matches`, `match_results`, `scored_results`, `states`)
- `WinProbabilityModelInput`: wraps the full payload + fitted artifact + optional model options
- `WinProbabilityModelInterface`: protocol with `name` and `predict(model_input)` methods

Built-in registered model names:

- `global`
- `bucketed`
- `hybrid`

The plot script resolves mode choices dynamically from the registry, so changing model logic or adding a new model implementation only requires registering it (no plot pipeline branching edits).

## Files

- `modeling/src/koris_api/win_probability.py`
  Core loading, state extraction, Elo fitting, bucketed logistic fitting, and evaluation helpers.
- `src/koris_api/win_probability.py`
  Compatibility shim so existing imports still work after the move.
- `modeling/scripts/train_win_probability_model.py`
  End-to-end training and validation script. It can optionally fetch missing play-by-play into a local cache before fitting.
- `modeling/scripts/plot_win_probability.py`
  Generates Yale-style win probability charts with time elapsed on the x-axis, and now also overlays a normalized point-difference probability curve.

## Example

```bash
PYTHONPATH=src .venv/bin/python modeling/scripts/train_win_probability_model.py \
  --season-file data/1div_24-25.json \
  --season-file data/1div_25-26.json \
  --train-season 2024-2025 \
  --validation-season 2025-2026 \
  --output-dir modeling/output/win_probability \
  --pbp-cache modeling/output/win_probability/playbyplay_cache.json \
  --fetch-missing-pbp
```

Outputs:

- `model_coefficients.json`
- `metrics.json`
- `validation_calibration.csv`
- `train_states.parquet`
- `validation_states.parquet`

## Example Plots

```bash
PYTHONPATH=src .venv/bin/python modeling/scripts/plot_win_probability.py \
  --model-dir modeling/output/win_probability \
  --season-file data/1div_24-25.json \
  --season-file data/1div_25-26.json \
  --pbp-cache modeling/output/win_probability/playbyplay_cache.json \
  --season 2025-2026 \
  --top-n-exciting 3 \
  --mode hybrid \
  --hybrid-cutoff-seconds 300 \
  --fit-point-diff-scale
```

The plotter can fit a per-panel score-differential normalization scale (`--fit-point-diff-scale`, enabled by default) so the normalized point-difference curve matches model prediction as closely as possible (minimum MSE over a scale grid). The chart annotations include fitted scale and prediction-vs-point-difference divergence metrics (MAE, RMSE, max absolute gap).
