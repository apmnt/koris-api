from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

import numpy as np
import pandas as pd

from koris_api.genius_api import GeniusSportsAPI

YALE_BUCKET_STARTS: list[int] = (
    list(range(0, 30)) + list(range(30, 62, 2)) + list(range(70, 2410, 10))
)

_CLOCK_RE = re.compile(r"^(\d+):(\d+)(?::\d+)?$")
_REG_PERIOD_RE = re.compile(r"^[PQ](\d+)$", re.IGNORECASE)
_OT_PERIOD_RE = re.compile(r"^OT(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class EloConfig:
    k_factor: float
    home_advantage: float
    carryover: float
    initial_rating: float = 1500.0
    scale: float = 400.0


@dataclass
class BucketedWinProbabilityModel:
    bucket_starts: list[int]
    coefficients: list[list[float]]
    global_coefficients: list[float]
    elo_config: EloConfig
    train_seasons: list[str]
    validation_seasons: list[str]

    def predict(
        self,
        seconds_remaining: int,
        score_diff: int,
        pre_game_prob: float,
    ) -> float:
        coeffs = self.coefficients[self._bucket_index(seconds_remaining)]
        return float(
            _sigmoid(
                coeffs[0]
                + coeffs[1] * float(score_diff)
                + coeffs[2] * float(pre_game_prob)
            )
        )

    def _bucket_index(self, seconds_remaining: int) -> int:
        index = int(
            np.searchsorted(self.bucket_starts, seconds_remaining, side="right")
        )
        return max(0, min(index - 1, len(self.bucket_starts) - 1))

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket_starts": self.bucket_starts,
            "feature_names": ["intercept", "score_diff", "pre_game_prob"],
            "global_coefficients": {
                "intercept": self.global_coefficients[0],
                "score_diff": self.global_coefficients[1],
                "pre_game_prob": self.global_coefficients[2],
            },
            "coefficients": [
                {
                    "bucket_start": bucket_start,
                    "intercept": coeffs[0],
                    "score_diff": coeffs[1],
                    "pre_game_prob": coeffs[2],
                }
                for bucket_start, coeffs in zip(self.bucket_starts, self.coefficients)
            ],
            "elo_config": asdict(self.elo_config),
            "train_seasons": self.train_seasons,
            "validation_seasons": self.validation_seasons,
        }


@dataclass(frozen=True)
class WinProbabilityPipelineData:
    matches: Sequence[dict[str, Any]]
    match_results: pd.DataFrame
    scored_results: pd.DataFrame
    states: pd.DataFrame


@dataclass(frozen=True)
class WinProbabilityModelInput:
    pipeline: WinProbabilityPipelineData
    artifact: BucketedWinProbabilityModel
    options: Mapping[str, Any] | None = None


class WinProbabilityModelInterface(Protocol):
    name: str

    def predict(self, model_input: WinProbabilityModelInput) -> np.ndarray: ...


class GlobalWinProbabilityPredictor:
    name = "global"

    def predict(self, model_input: WinProbabilityModelInput) -> np.ndarray:
        return predict_global_probabilities(
            model_input.pipeline.states,
            model_input.artifact.global_coefficients,
        )


class BucketedWinProbabilityPredictor:
    name = "bucketed"

    def predict(self, model_input: WinProbabilityModelInput) -> np.ndarray:
        return predict_state_probabilities(
            model_input.pipeline.states,
            model_input.artifact,
        )


class HybridWinProbabilityPredictor:
    name = "hybrid"

    def predict(self, model_input: WinProbabilityModelInput) -> np.ndarray:
        cutoff_seconds = _resolve_int_option(
            model_input,
            "hybrid_cutoff_seconds",
            default=300,
        )
        global_predictions = predict_global_probabilities(
            model_input.pipeline.states,
            model_input.artifact.global_coefficients,
        )
        bucketed_predictions = predict_state_probabilities(
            model_input.pipeline.states,
            model_input.artifact,
        )
        hybrid_predictions = global_predictions.copy()
        mask = (
            model_input.pipeline.states["seconds_remaining"].to_numpy(dtype=int)
            <= cutoff_seconds
        )
        hybrid_predictions[mask] = bucketed_predictions[mask]
        return hybrid_predictions


WIN_PROBABILITY_MODEL_REGISTRY: dict[str, WinProbabilityModelInterface] = {}


def register_win_probability_model(model: WinProbabilityModelInterface) -> None:
    key = model.name.strip().lower()
    if not key:
        raise ValueError("Model name must be a non-empty string.")
    WIN_PROBABILITY_MODEL_REGISTRY[key] = model


def available_win_probability_models() -> list[str]:
    return sorted(WIN_PROBABILITY_MODEL_REGISTRY.keys())


def predict_with_win_probability_model(
    model_name: str,
    model_input: WinProbabilityModelInput,
) -> np.ndarray:
    key = model_name.strip().lower()
    model = WIN_PROBABILITY_MODEL_REGISTRY.get(key)
    if model is None:
        available = ", ".join(available_win_probability_models())
        raise ValueError(f"Unknown win-probability model '{model_name}'. Available: {available}")

    predictions = np.asarray(model.predict(model_input), dtype=float)
    if predictions.ndim != 1 or predictions.shape[0] != len(model_input.pipeline.states):
        raise ValueError(
            "Model prediction must return a 1D array with one value per input row."
        )
    return np.clip(predictions, 1e-6, 1 - 1e-6)


def _resolve_int_option(
    model_input: WinProbabilityModelInput,
    option_key: str,
    *,
    default: int,
) -> int:
    if model_input.options is None or option_key not in model_input.options:
        return default
    value = model_input.options[option_key]
    if not isinstance(value, int):
        raise TypeError(f"Model option '{option_key}' must be an int, got {type(value).__name__}.")
    return value


def _register_default_win_probability_models() -> None:
    register_win_probability_model(GlobalWinProbabilityPredictor())
    register_win_probability_model(BucketedWinProbabilityPredictor())
    register_win_probability_model(HybridWinProbabilityPredictor())


_register_default_win_probability_models()


def load_matches(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            raw_matches = payload.get("matches", [])
        elif isinstance(payload, list):
            raw_matches = payload
        else:
            raise ValueError(f"Unsupported match payload in {path}")

        for raw_match in raw_matches:
            if not isinstance(raw_match, dict):
                continue
            matches.append(_normalize_match(raw_match, path))
    return matches


def load_playbyplay_cache(
    cache_path: str | Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    path = Path(cache_path)
    if not path.exists():
        return {}, {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "playbyplay" in payload:
        return (
            {str(key): value for key, value in payload.get("playbyplay", {}).items()},
            {str(key): value for key, value in payload.get("errors", {}).items()},
        )
    return {}, {}


def save_playbyplay_cache(
    cache_path: str | Path,
    playbyplay_by_match_id: dict[str, Any],
    errors_by_match_id: dict[str, str],
) -> None:
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "playbyplay": playbyplay_by_match_id,
        "errors": errors_by_match_id,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def fetch_missing_playbyplay(
    matches: Sequence[dict[str, Any]],
    cache_path: str | Path,
    competition_id: str = "42145",
    max_workers: int = 8,
) -> tuple[dict[str, Any], dict[str, str]]:
    playbyplay_by_match_id, errors_by_match_id = load_playbyplay_cache(cache_path)

    fetch_targets = sorted(
        {
            (
                str(match["match_id"]),
                str(match.get("match_external_id") or match["match_id"]),
            )
            for match in matches
            if match.get("match_id")
            and not isinstance(match.get("playbyplay"), dict)
            and str(match["match_id"]) not in playbyplay_by_match_id
        }
    )
    if not fetch_targets:
        return playbyplay_by_match_id, errors_by_match_id

    def _fetch(
        cache_match_id: str,
        external_match_id: str,
    ) -> tuple[str, Optional[dict[str, Any]], Optional[str]]:
        try:
            playbyplay = GeniusSportsAPI.get_match_playbyplay(
                match_id=external_match_id,
                competition_id=competition_id,
            )
            return cache_match_id, playbyplay, None
        except Exception as exc:
            return cache_match_id, None, str(exc)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch, cache_match_id, external_match_id): cache_match_id
            for cache_match_id, external_match_id in fetch_targets
        }
        for future in as_completed(futures):
            match_id, playbyplay, error = future.result()
            if playbyplay is not None:
                playbyplay_by_match_id[match_id] = playbyplay
                errors_by_match_id.pop(match_id, None)
            elif error:
                errors_by_match_id[match_id] = error

    save_playbyplay_cache(cache_path, playbyplay_by_match_id, errors_by_match_id)
    return playbyplay_by_match_id, errors_by_match_id


def attach_playbyplay_cache(
    matches: Sequence[dict[str, Any]],
    playbyplay_by_match_id: dict[str, Any],
) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    for match in matches:
        enriched = dict(match)
        if not isinstance(enriched.get("playbyplay"), dict):
            cached = playbyplay_by_match_id.get(str(enriched.get("match_id")))
            if isinstance(cached, dict):
                enriched["playbyplay"] = cached
        hydrated.append(enriched)
    return hydrated


def build_match_results_frame(matches: Sequence[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for match in matches:
        home_score = _coerce_int(match.get("home_score"))
        away_score = _coerce_int(match.get("away_score"))
        if home_score is None or away_score is None:
            continue

        rows.append(
            {
                "match_id": str(match["match_id"]),
                "season": str(match["season"]),
                "match_datetime": _parse_match_datetime(
                    match.get("date"),
                    match.get("time"),
                ),
                "home_team_id": str(match["home_team_id"]),
                "home_team": match["home_team"],
                "away_team_id": str(match["away_team_id"]),
                "away_team": match["away_team"],
                "home_score": home_score,
                "away_score": away_score,
                "home_win": int(home_score > away_score),
            }
        )

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame = frame.sort_values(
        by=["match_datetime", "season", "match_id"],
        kind="stable",
    ).reset_index(drop=True)
    return frame


def tune_elo_config(
    matches: pd.DataFrame,
    k_factors: Sequence[float] = (12.0, 18.0, 24.0, 30.0),
    home_advantages: Sequence[float] = (0.0, 40.0, 80.0, 120.0),
    carryovers: Sequence[float] = (0.5, 0.7, 0.85, 1.0),
) -> EloConfig:
    if matches.empty:
        raise ValueError("Cannot tune Elo without match results.")

    best_config: Optional[EloConfig] = None
    best_loss = math.inf

    for k_factor, home_advantage, carryover in product(
        k_factors,
        home_advantages,
        carryovers,
    ):
        config = EloConfig(
            k_factor=float(k_factor),
            home_advantage=float(home_advantage),
            carryover=float(carryover),
        )
        scored = compute_elo_probabilities(matches, config)
        loss = log_loss(
            scored["home_win"].to_numpy(dtype=float),
            scored["pregame_home_wp"].to_numpy(dtype=float),
        )
        if loss < best_loss:
            best_loss = loss
            best_config = config

    if best_config is None:
        raise ValueError("Elo tuning did not produce a configuration.")
    return best_config


def compute_elo_probabilities(
    matches: pd.DataFrame,
    config: EloConfig,
) -> pd.DataFrame:
    if matches.empty:
        return matches.copy()

    frame = matches.copy().sort_values(
        by=["match_datetime", "season", "match_id"],
        kind="stable",
    )
    ratings: dict[str, float] = {}
    current_season: Optional[str] = None
    pregame_home_wp: list[float] = []

    for row in frame.itertuples(index=False):
        season = str(row.season)
        if season != current_season:
            if current_season is not None:
                ratings = {
                    team_id: config.initial_rating
                    + config.carryover * (rating - config.initial_rating)
                    for team_id, rating in ratings.items()
                }
            current_season = season

        home_team_id = str(row.home_team_id)
        away_team_id = str(row.away_team_id)
        home_rating = ratings.get(home_team_id, config.initial_rating)
        away_rating = ratings.get(away_team_id, config.initial_rating)

        probability = _elo_expected_score(home_rating, away_rating, config)
        pregame_home_wp.append(probability)

        delta = config.k_factor * (float(row.home_win) - probability)
        ratings[home_team_id] = home_rating + delta
        ratings[away_team_id] = away_rating - delta

    frame["pregame_home_wp"] = pregame_home_wp
    return frame


def build_state_frame(
    matches: Sequence[dict[str, Any]],
    pregame_home_wp_by_match_id: dict[str, float],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for match in matches:
        match_id = str(match.get("match_id"))
        pregame_home_wp = pregame_home_wp_by_match_id.get(match_id)
        if pregame_home_wp is None:
            continue
        rows.extend(_build_match_state_rows(match, pregame_home_wp))

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame = frame.sort_values(
        by=["match_datetime", "match_id", "event_order"],
        kind="stable",
    ).reset_index(drop=True)
    return frame


def fit_bucketed_model(
    train_states: pd.DataFrame,
    bucket_starts: Sequence[int] = YALE_BUCKET_STARTS,
    min_bucket_samples: int = 60,
    l2_penalty: float = 1e-3,
) -> BucketedWinProbabilityModel:
    if train_states.empty:
        raise ValueError("Cannot fit win-probability model without state rows.")

    feature_matrix = _design_matrix(train_states)
    labels = train_states["home_win"].to_numpy(dtype=float)
    global_coefficients = _fit_logistic_regression(
        feature_matrix,
        labels,
        l2_penalty=l2_penalty,
    )

    coefficients: list[list[float]] = []
    previous_coefficients: Optional[np.ndarray] = None

    for bucket_start, bucket_end in _iter_bucket_ranges(bucket_starts):
        bucket_rows = train_states[
            (train_states["seconds_remaining"] >= bucket_start)
            & (train_states["seconds_remaining"] < bucket_end)
        ]

        if bucket_rows.empty:
            fallback = (
                previous_coefficients
                if previous_coefficients is not None
                else global_coefficients
            )
            coefficients.append(fallback.astype(float).tolist())
            previous_coefficients = fallback
            continue

        bucket_features = _design_matrix(bucket_rows)
        bucket_labels = bucket_rows["home_win"].to_numpy(dtype=float)

        if (
            len(bucket_rows) < min_bucket_samples
            or bucket_rows["home_win"].nunique(dropna=True) < 2
        ):
            fallback = global_coefficients.copy()
        else:
            fallback = _fit_logistic_regression(
                bucket_features,
                bucket_labels,
                l2_penalty=l2_penalty,
                initial_coefficients=global_coefficients,
            )

        coefficients.append(fallback.astype(float).tolist())
        previous_coefficients = fallback

    dummy_config = EloConfig(k_factor=0.0, home_advantage=0.0, carryover=1.0)
    return BucketedWinProbabilityModel(
        bucket_starts=list(bucket_starts),
        coefficients=coefficients,
        global_coefficients=global_coefficients.astype(float).tolist(),
        elo_config=dummy_config,
        train_seasons=[],
        validation_seasons=[],
    )


def predict_state_probabilities(
    states: pd.DataFrame,
    model: BucketedWinProbabilityModel,
) -> np.ndarray:
    predictions = np.zeros(len(states), dtype=float)
    for index, row in enumerate(states.itertuples(index=False)):
        predictions[index] = model.predict(
            seconds_remaining=int(row.seconds_remaining),
            score_diff=int(row.score_diff),
            pre_game_prob=float(row.pre_game_prob),
        )
    return np.clip(predictions, 1e-6, 1 - 1e-6)


def predict_global_probabilities(
    states: pd.DataFrame,
    global_coefficients: Sequence[float],
) -> np.ndarray:
    matrix = _design_matrix(states)
    coefficients = np.asarray(global_coefficients, dtype=float)
    return np.clip(_sigmoid(matrix @ coefficients), 1e-6, 1 - 1e-6)


def calibration_table(
    labels: Sequence[float],
    probabilities: Sequence[float],
    bins: int = 10,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "actual": np.asarray(labels, dtype=float),
            "predicted": np.asarray(probabilities, dtype=float),
        }
    )
    edges = np.linspace(0.0, 1.0, bins + 1)
    frame["bin"] = pd.cut(
        frame["predicted"],
        bins=edges,
        include_lowest=True,
        right=True,
    )

    summary = (
        frame.groupby("bin", observed=False)
        .agg(
            count=("actual", "size"),
            avg_prediction=("predicted", "mean"),
            observed_win_rate=("actual", "mean"),
        )
        .reset_index()
    )
    summary["bin_lower"] = [interval.left for interval in summary["bin"]]
    summary["bin_upper"] = [interval.right for interval in summary["bin"]]
    return summary[
        ["bin_lower", "bin_upper", "count", "avg_prediction", "observed_win_rate"]
    ]


def accuracy_score(labels: Sequence[float], probabilities: Sequence[float]) -> float:
    predicted = np.asarray(probabilities, dtype=float) >= 0.5
    actual = np.asarray(labels, dtype=float) >= 0.5
    return float(np.mean(predicted == actual))


def brier_score(labels: Sequence[float], probabilities: Sequence[float]) -> float:
    actual = np.asarray(labels, dtype=float)
    predicted = np.asarray(probabilities, dtype=float)
    return float(np.mean((predicted - actual) ** 2))


def log_loss(labels: Sequence[float], probabilities: Sequence[float]) -> float:
    actual = np.asarray(labels, dtype=float)
    predicted = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    return float(
        -np.mean(actual * np.log(predicted) + (1 - actual) * np.log(1 - predicted))
    )


def build_model_artifact(
    fitted_model: BucketedWinProbabilityModel,
    elo_config: EloConfig,
    train_seasons: Sequence[str],
    validation_seasons: Sequence[str],
) -> BucketedWinProbabilityModel:
    return BucketedWinProbabilityModel(
        bucket_starts=fitted_model.bucket_starts,
        coefficients=fitted_model.coefficients,
        global_coefficients=fitted_model.global_coefficients,
        elo_config=elo_config,
        train_seasons=[str(season) for season in train_seasons],
        validation_seasons=[str(season) for season in validation_seasons],
    )


def _normalize_match(raw_match: dict[str, Any], source_path: Path) -> dict[str, Any]:
    match_id = _pick(raw_match, "match_id", "Match ID")
    home_team = _pick(raw_match, "home_team", "Home Team")
    away_team = _pick(raw_match, "away_team", "Away Team")
    home_team_id = _pick(raw_match, "home_team_id", "Home Team ID", default=home_team)
    away_team_id = _pick(raw_match, "away_team_id", "Away Team ID", default=away_team)
    season = _pick(raw_match, "season", "Season")

    if match_id is None or home_team is None or away_team is None or season is None:
        raise ValueError(f"Missing required match fields in {source_path}")

    return {
        "match_id": str(match_id),
        "match_external_id": _pick(raw_match, "match_external_id", "Match External ID"),
        "date": _pick(raw_match, "date", "Date"),
        "time": _pick(raw_match, "time", "Time"),
        "season": str(season),
        "home_team": str(home_team),
        "home_team_id": str(home_team_id),
        "away_team": str(away_team),
        "away_team_id": str(away_team_id),
        "home_score": _pick(raw_match, "home_score", "Home Score"),
        "away_score": _pick(raw_match, "away_score", "Away Score"),
        "playbyplay": raw_match.get("playbyplay"),
        "source_path": str(source_path),
    }


def _pick(raw_match: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = raw_match.get(key)
        if value not in (None, ""):
            return value
    return default


def _parse_match_datetime(date_value: Any, time_value: Any) -> datetime:
    date_text = str(date_value or "").strip()
    time_text = str(time_value or "").strip()
    combined = " ".join(part for part in [date_text, time_text] if part)

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(combined or date_text, fmt)
        except ValueError:
            continue

    return datetime.min


def _coerce_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_match_state_rows(
    match: dict[str, Any],
    pregame_home_wp: float,
) -> list[dict[str, Any]]:
    playbyplay = match.get("playbyplay")
    if not isinstance(playbyplay, dict):
        return []

    home_score = _coerce_int(match.get("home_score"))
    away_score = _coerce_int(match.get("away_score"))
    if home_score is None or away_score is None:
        return []

    raw_events = playbyplay.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        return []

    terminal_score = f"{home_score}-{away_score}"
    trimmed_events = _trim_events_to_terminal_game(raw_events, terminal_score)

    timed_rows: list[dict[str, Any]] = []
    for original_index, event in enumerate(trimmed_events):
        if not isinstance(event, dict):
            continue
        seconds_remaining = _seconds_remaining(event.get("period"), event.get("time"))
        if seconds_remaining is None:
            continue

        score = _parse_score(event.get("score"))
        if score is None:
            continue

        home_state_score, away_state_score = score
        period_index = _period_index(event.get("period"))
        if period_index is None:
            continue

        timed_rows.append(
            {
                "match_id": str(match["match_id"]),
                "season": str(match["season"]),
                "match_datetime": _parse_match_datetime(
                    match.get("date"),
                    match.get("time"),
                ),
                "event_order": original_index,
                "period_index": period_index,
                "clock_seconds": _clock_seconds(event.get("time")) or 0,
                "seconds_remaining": seconds_remaining,
                "score_diff": home_state_score - away_state_score,
                "pre_game_prob": float(pregame_home_wp),
                "home_win": int(home_score > away_score),
            }
        )

    timed_rows.sort(
        key=lambda row: (row["period_index"], -row["clock_seconds"], row["event_order"])
    )

    if not timed_rows:
        return []

    final_score_diff = home_score - away_score
    last_row = timed_rows[-1]
    if last_row["seconds_remaining"] != 0 or last_row["score_diff"] != final_score_diff:
        timed_rows.append(
            {
                "match_id": str(match["match_id"]),
                "season": str(match["season"]),
                "match_datetime": _parse_match_datetime(
                    match.get("date"),
                    match.get("time"),
                ),
                "event_order": timed_rows[-1]["event_order"] + 1,
                "period_index": max(timed_rows[-1]["period_index"], 4),
                "clock_seconds": 0,
                "seconds_remaining": 0,
                "score_diff": final_score_diff,
                "pre_game_prob": float(pregame_home_wp),
                "home_win": int(home_score > away_score),
            }
        )

    return timed_rows


def _trim_events_to_terminal_game(
    raw_events: Sequence[dict[str, Any]],
    terminal_score: str,
) -> list[dict[str, Any]]:
    scoring_seen = False
    for index, event in enumerate(raw_events):
        score = event.get("score")
        if isinstance(score, str) and score != "0-0":
            scoring_seen = True
        if (
            scoring_seen
            and event.get("event_type") == "game"
            and score == terminal_score
        ):
            return list(raw_events[: index + 1])
    return list(raw_events)


def _parse_score(score_value: Any) -> Optional[tuple[int, int]]:
    if not isinstance(score_value, str) or "-" not in score_value:
        return None
    home_text, away_text = score_value.split("-", 1)
    home_score = _coerce_int(home_text)
    away_score = _coerce_int(away_text)
    if home_score is None or away_score is None:
        return None
    return home_score, away_score


def _clock_seconds(time_value: Any) -> Optional[int]:
    if not isinstance(time_value, str):
        return None
    match = _CLOCK_RE.match(time_value.strip())
    if not match:
        return None
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    return minutes * 60 + seconds


def _period_index(period_value: Any) -> Optional[int]:
    if not isinstance(period_value, str):
        return None
    text = period_value.strip().upper()
    reg_match = _REG_PERIOD_RE.match(text)
    if reg_match:
        return int(reg_match.group(1))
    ot_match = _OT_PERIOD_RE.match(text)
    if ot_match:
        return 4 + int(ot_match.group(1))
    return None


def _seconds_remaining(period_value: Any, time_value: Any) -> Optional[int]:
    period_index = _period_index(period_value)
    clock_seconds = _clock_seconds(time_value)
    if period_index is None or clock_seconds is None:
        return None

    if period_index <= 4:
        return (4 - period_index) * 600 + clock_seconds
    return min(clock_seconds, 300)


def _elo_expected_score(
    home_rating: float, away_rating: float, config: EloConfig
) -> float:
    exponent = -((home_rating + config.home_advantage) - away_rating) / config.scale
    return float(1.0 / (1.0 + 10.0**exponent))


def _iter_bucket_ranges(bucket_starts: Sequence[int]) -> Iterable[tuple[int, int]]:
    starts = list(bucket_starts)
    for index, bucket_start in enumerate(starts):
        if index + 1 < len(starts):
            bucket_end = starts[index + 1]
        else:
            bucket_end = starts[-1] + 1
        yield int(bucket_start), int(bucket_end)


def _design_matrix(states: pd.DataFrame) -> np.ndarray:
    return np.column_stack(
        [
            np.ones(len(states), dtype=float),
            states["score_diff"].to_numpy(dtype=float),
            states["pre_game_prob"].to_numpy(dtype=float),
        ]
    )


def _fit_logistic_regression(
    features: np.ndarray,
    labels: np.ndarray,
    l2_penalty: float,
    initial_coefficients: Optional[np.ndarray] = None,
    max_iterations: int = 100,
    tolerance: float = 1e-7,
) -> np.ndarray:
    coefficients = (
        np.asarray(initial_coefficients, dtype=float).copy()
        if initial_coefficients is not None
        else np.zeros(features.shape[1], dtype=float)
    )

    if initial_coefficients is None:
        mean_target = float(
            np.clip((labels.mean() if len(labels) else 0.5), 1e-6, 1 - 1e-6)
        )
        coefficients[0] = math.log(mean_target / (1.0 - mean_target))

    penalty = np.full(features.shape[1], l2_penalty, dtype=float)
    penalty[0] = 0.0

    for _ in range(max_iterations):
        linear = features @ coefficients
        probabilities = _sigmoid(linear)
        gradient = features.T @ (probabilities - labels) + penalty * coefficients
        weights = np.clip(probabilities * (1.0 - probabilities), 1e-6, None)
        hessian = features.T @ (weights[:, None] * features)
        hessian += np.diag(penalty)

        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient

        coefficients -= step
        if float(np.max(np.abs(step))) < tolerance:
            break

    return coefficients


def _sigmoid(values: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -30.0, 30.0)))
