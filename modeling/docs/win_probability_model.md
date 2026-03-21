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

## Files

- `modeling/src/koris_api/win_probability.py`
  Core loading, state extraction, Elo fitting, bucketed logistic fitting, and evaluation helpers.
- `src/koris_api/win_probability.py`
  Compatibility shim so existing imports still work after the move.
- `modeling/scripts/train_win_probability_model.py`
  End-to-end training and validation script. It can optionally fetch missing play-by-play into a local cache before fitting.
- `modeling/scripts/plot_win_probability.py`
  Generates Yale-style win probability charts with time elapsed on the x-axis.

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
  --hybrid-cutoff-seconds 300
```
