from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .win_probability import (
    BucketedWinProbabilityModel,
    EloConfig,
    WinProbabilityModelInput,
    WinProbabilityPipelineData,
    attach_playbyplay_cache,
    build_match_results_frame,
    build_state_frame,
    compute_elo_probabilities,
    load_matches,
    load_playbyplay_cache,
    predict_with_win_probability_model,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_MODE = "hybrid"
DEFAULT_MODEL_DIR = PACKAGE_ROOT / "modeling" / "output" / "win_probability_1div"
DEFAULT_DATA_DIR = PACKAGE_ROOT / "data"
DEFAULT_COMPETITION_ID = "42145"
SEASON_FILENAME_RE = re.compile(r"^(?P<league>[a-z0-9-]+)_(?P<season>\d{2}-\d{2})\.json$")


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _sort_key(path: Path) -> tuple[int, int]:
    match = SEASON_FILENAME_RE.match(path.name)
    if not match:
        return (-1, -1)
    y1, y2 = match.group("season").split("-")
    return (int(y1), int(y2))


def detect_season_files(
    *,
    data_dir: Path,
    league_prefix: str,
    latest_seasons: int,
) -> list[Path]:
    candidates = []
    for path in sorted(data_dir.glob(f"{league_prefix}_*.json")):
        if SEASON_FILENAME_RE.match(path.name):
            candidates.append(path)
    candidates.sort(key=_sort_key, reverse=True)
    selected = candidates[: max(1, latest_seasons)]
    return list(reversed(selected))


def load_model(model_dir: Path) -> BucketedWinProbabilityModel:
    payload = json.loads((model_dir / "model_coefficients.json").read_text())
    elo_config = EloConfig(**payload["elo_config"])
    return BucketedWinProbabilityModel(
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


def build_pipeline_data(
    *,
    matches: list[dict[str, Any]],
    model: BucketedWinProbabilityModel,
) -> WinProbabilityPipelineData:
    results = build_match_results_frame(matches)
    scored_results = compute_elo_probabilities(results, model.elo_config)
    pregame_home_wp_by_match_id = dict(
        zip(scored_results["match_id"], scored_results["pregame_home_wp"])
    )
    states = build_state_frame(matches, pregame_home_wp_by_match_id)
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


def smooth_step_series(
    frame: pd.DataFrame,
    values: np.ndarray,
    *,
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


def normalize_score_diff_probability(score_diff: np.ndarray, scale: float) -> np.ndarray:
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
    return (
        float(np.mean(np.abs(diff))),
        float(np.sqrt(np.mean(np.square(diff)))),
        float(np.max(np.abs(diff))),
    )


def downsample_indices(total_seconds: int, step_seconds: int) -> list[int]:
    indices = list(range(0, total_seconds + 1, max(1, step_seconds)))
    if indices[-1] != total_seconds:
        indices.append(total_seconds)
    return indices


def round_probability(value: float) -> float:
    return round(float(value), 6)


def round_score_diff(value: float) -> float:
    return round(float(value), 3)


def round_pp(value: float) -> float:
    return round(float(value) * 100.0, 3)


def _normalize_team_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _period_label(period_value: Any, period_type: Any) -> str | None:
    period_index = _as_int(period_value)
    if period_index is None:
        return None
    normalized_period_type = str(period_type or "REGULAR").strip().upper()
    if normalized_period_type == "REGULAR":
        return f"P{period_index}"
    if normalized_period_type in {"OVERTIME", "EXTRATIME"}:
        return f"OT{max(1, period_index - 4)}"
    return f"P{period_index}"


def _mmss_to_seconds(clock_value: Any) -> int | None:
    if not isinstance(clock_value, str):
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", clock_value.strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9,fi;q=0.8",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _live_events_from_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events = data.get("pbp")
    if isinstance(raw_events, dict):
        raw_events = list(raw_events.values())
    if not isinstance(raw_events, list):
        return []

    def _action_number(event: Any) -> int:
        if not isinstance(event, dict):
            return -1
        return _as_int(event.get("actionNumber")) or -1

    normalized: list[dict[str, Any]] = []
    for event in sorted(raw_events, key=_action_number):
        if not isinstance(event, dict):
            continue
        period = _period_label(event.get("period"), event.get("periodType"))
        gt = str(event.get("gt") or "").strip()
        if not period or not gt:
            continue
        s1 = _as_int(event.get("s1"))
        s2 = _as_int(event.get("s2"))
        if s1 is None or s2 is None:
            continue
        normalized.append(
            {
                "event_type": str(event.get("actionType") or "").strip().lower(),
                "period": period,
                "team": (
                    str(_as_int(event.get("tno")))
                    if _as_int(event.get("tno")) in {0, 1, 2}
                    else None
                ),
                "time": gt,
                "score": f"{s1}-{s2}",
                "action": str(event.get("subType") or event.get("actionType") or ""),
            }
        )
    return normalized


def _resolve_team_id(matches: list[dict[str, Any]], team_name: str) -> str:
    normalized_target = _normalize_team_name(team_name)
    for match in matches:
        home_name = str(match.get("home_team") or "")
        away_name = str(match.get("away_team") or "")
        if _normalize_team_name(home_name) == normalized_target:
            return str(match.get("home_team_id") or team_name)
        if _normalize_team_name(away_name) == normalized_target:
            return str(match.get("away_team_id") or team_name)
    return team_name


def _build_live_match(
    *,
    match_id: str,
    competition_id: str,
    date: str,
    time: str,
    season: str,
    home_team: str,
    away_team: str,
    matches: list[dict[str, Any]],
    live_data_url: str | None,
) -> dict[str, Any]:
    del competition_id
    resolved_live_data_url = (
        live_data_url
        or f"https://fibalivestats.dcd.shared.geniussports.com/data/{match_id}/data.json"
    )
    live_data = _fetch_json(resolved_live_data_url)
    home_payload = live_data.get("tm", {}).get("1", {})
    away_payload = live_data.get("tm", {}).get("2", {})

    home_score = _as_int(home_payload.get("score")) or 0
    away_score = _as_int(away_payload.get("score")) or 0
    events = _live_events_from_data(live_data)
    if not events:
        raise ValueError(f"No live events found for match {match_id}")

    current_period = _as_int(live_data.get("period")) or 1
    current_clock_seconds = _mmss_to_seconds(live_data.get("clock")) or 0
    current_elapsed_seconds = elapsed_seconds(current_period, current_clock_seconds)

    return {
        "match_id": str(match_id),
        "match_external_id": str(match_id),
        "date": str(date),
        "time": str(time),
        "season": str(season),
        "home_team": str(home_team),
        "home_team_id": _resolve_team_id(matches, str(home_team)),
        "away_team": str(away_team),
        "away_team_id": _resolve_team_id(matches, str(away_team)),
        "home_score": home_score,
        "away_score": away_score,
        "playbyplay": {
            "events": events,
        },
        "_current_elapsed_seconds": current_elapsed_seconds,
    }


def build_live_game_prediction(
    *,
    match_id: str,
    home_team: str,
    away_team: str,
    season: str,
    date: str,
    time: str,
    competition_id: str = DEFAULT_COMPETITION_ID,
    live_data_url: str | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
    pbp_cache: str | Path | None = None,
    season_files: list[str | Path] | None = None,
    league_prefix: str = "1div",
    latest_seasons: int = 2,
    mode: str = DEFAULT_MODEL_MODE,
    hybrid_cutoff_seconds: int = 300,
    smooth_window_seconds: int = 30,
    downsample_seconds: int = 5,
    point_diff_scale: float = 8.0,
    fit_point_diff_scale_enabled: bool = True,
    point_diff_scale_min: float = 1.0,
    point_diff_scale_max: float = 40.0,
    point_diff_scale_step: float = 0.25,
) -> dict[str, Any]:
    data_dir_path = Path(data_dir)
    model_dir_path = Path(model_dir)
    season_file_paths = (
        [Path(path) for path in season_files]
        if season_files
        else detect_season_files(
            data_dir=data_dir_path,
            league_prefix=league_prefix,
            latest_seasons=latest_seasons,
        )
    )
    if not season_file_paths:
        raise ValueError("No season files were found for live match prediction export.")

    model = load_model(model_dir_path)
    matches = load_matches(season_file_paths)
    pbp_cache_path = Path(pbp_cache) if pbp_cache else model_dir_path / "playbyplay_cache.json"
    playbyplay_by_match_id, _ = load_playbyplay_cache(pbp_cache_path)
    matches = attach_playbyplay_cache(matches, playbyplay_by_match_id)
    live_match = _build_live_match(
        match_id=match_id,
        competition_id=competition_id,
        date=date,
        time=time,
        season=season,
        home_team=home_team,
        away_team=away_team,
        matches=matches,
        live_data_url=live_data_url,
    )

    filtered_matches = [
        match
        for match in matches
        if str(match.get("match_id")) != str(match_id)
        and str(match.get("match_external_id") or "") != str(match_id)
    ]
    pipeline_matches = [*filtered_matches, live_match]
    pipeline_data = build_pipeline_data(matches=pipeline_matches, model=model)
    states = pipeline_data.states.copy()
    if states.empty:
        raise ValueError("No state rows were available for live match prediction export.")

    model_input = WinProbabilityModelInput(
        pipeline=pipeline_data,
        artifact=model,
        options={"hybrid_cutoff_seconds": hybrid_cutoff_seconds},
    )
    probability_column = f"home_win_probability_{mode}"
    states[probability_column] = predict_with_win_probability_model(mode, model_input)
    states["elapsed_seconds"] = [
        elapsed_seconds(int(period_index), int(clock_seconds))
        for period_index, clock_seconds in zip(
            states["period_index"],
            states["clock_seconds"],
        )
    ]

    live_frame = states[states["match_id"] == str(match_id)].copy()
    if live_frame.empty:
        raise ValueError(f"No modeled states were produced for live match {match_id}")

    current_elapsed_seconds = _as_int(live_match.get("_current_elapsed_seconds"))
    if current_elapsed_seconds is not None:
        live_frame = live_frame[live_frame["elapsed_seconds"] <= current_elapsed_seconds]
    if live_frame.empty:
        raise ValueError(f"No live states remained after trimming live match {match_id}")

    live_frame = live_frame.sort_values(
        by=["elapsed_seconds", "event_order"],
        kind="stable",
    )
    total_seconds = int(live_frame["elapsed_seconds"].max())
    home_win_probability = live_frame[probability_column].to_numpy(dtype=float)
    score_diff = live_frame["score_diff"].to_numpy(dtype=float)

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
        frame=live_frame,
        values=home_win_probability,
        total_seconds=total_seconds,
        window_seconds=smooth_window_seconds,
    )
    _, smooth_normalized_point_diff_probability = smooth_step_series(
        frame=live_frame,
        values=normalized_point_diff_probability,
        total_seconds=total_seconds,
        window_seconds=smooth_window_seconds,
    )
    _, smooth_score_diff = smooth_step_series(
        frame=live_frame,
        values=score_diff,
        total_seconds=total_seconds,
        window_seconds=smooth_window_seconds,
    )

    samples = downsample_indices(total_seconds, downsample_seconds)
    timeline = [
        {
            "elapsed_seconds": int(index),
            "prediction_home_wp": round_probability(smooth_home_probability[index]),
            "normalized_point_diff_home_wp": round_probability(
                smooth_normalized_point_diff_probability[index]
            ),
            "score_diff": round_score_diff(smooth_score_diff[index]),
        }
        for index in samples
    ]

    pregame_lookup = dict(
        zip(
            pipeline_data.scored_results["match_id"],
            pipeline_data.scored_results["pregame_home_wp"],
        )
    )
    generated_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    return {
        "generated_at": generated_at,
        "match_id": str(match_id),
        "season": str(season),
        "date": str(date),
        "time": str(time),
        "home_team": str(home_team),
        "away_team": str(away_team),
        "home_score": int(live_match["home_score"]),
        "away_score": int(live_match["away_score"]),
        "pregame_home_wp": round_probability(
            float(pregame_lookup.get(str(match_id), home_win_probability[0]))
        ),
        "current_home_wp": round_probability(float(home_win_probability[-1])),
        "fitted_scale": round(float(fitted_scale), 3),
        "mae_pp": round_pp(mae),
        "rmse_pp": round_pp(rmse),
        "max_pp": round_pp(max_abs),
        "timeline": timeline,
    }


__all__ = [
    "DEFAULT_COMPETITION_ID",
    "DEFAULT_DATA_DIR",
    "DEFAULT_MODEL_DIR",
    "DEFAULT_MODEL_MODE",
    "build_live_game_prediction",
    "detect_season_files",
]
