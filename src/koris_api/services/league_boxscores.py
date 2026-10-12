import json
import logging
import time
from pathlib import Path
from typing import Any

from ..basketfi_api import BasketFiAPI
from ..basketfi_parser import BasketFiParser
from ..pipeline.types import BatchReport, Component, GameOutcome
from ..seasons import current_season_id
from .baskethotel_season_boxscores import download_baskethotel_season_boxscores
from .common import (
    _augment_seasons_with_baskethotel,
    _extract_season_start_year,
    _fetch_historical_matches,
    _is_historical_season,
)
from .game_exports import enrich_matches, season_reference
from .season_boxscores import download_matches_with_boxscores
from .season_comprehensive import download_season_comprehensive


def download_league_boxscores_all_seasons(
    category_id: str,
    output_dir: str,
    start_year: int = 2010,
    limit_seasons: int | None = None,
    combine_output: bool = False,
    combined_file: str | None = None,
    max_workers: int = 5,
    verbose: bool = True,
) -> int:
    """
    Download boxscores + player data and return the number of failed games/teams.

    Historical seasons (through 2023) are fetched from BasketHotel.
    Modern seasons (2024+) use season-comprehensive with advanced boxscores.
    """
    if limit_seasons is not None and limit_seasons < 0:
        raise ValueError("limit_seasons must be nonnegative")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"\n{'=' * 80}")
        print("LEAGUE BOXSCORES (ALL SEASONS)")
        print(f"{'=' * 80}")
        print(f"Category ID: {category_id}")
        print(f"Start year: {start_year}")
        print(f"Output directory: {output_path.absolute()}")
        print(f"{'=' * 80}\n")

    try:
        category_data = BasketFiAPI.get_category(current_season_id(), category_id)
    except Exception as e:
        logging.getLogger(__name__).debug(
            "league_boxscores: operation failed", exc_info=True
        )
        raise RuntimeError("Failed to fetch category information") from e

    if "category" not in category_data or "seasons" not in category_data["category"]:
        raise ValueError(f"No seasons in category {category_id}")

    category = category_data["category"]
    category_name = category.get("category_name", str(category_id))
    safe_category = (
        category_name.lower().replace(" ", "_").replace("/", "_").replace("__", "_")
    )

    seasons = category["seasons"]
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)

    filtered = []
    for season in seasons:
        season_name = season.get("season_name")
        season_year = _extract_season_start_year(season_name)
        if season_year is None or season_year < start_year:
            continue
        filtered.append(season)

    if not filtered:
        print("No seasons found for the requested year range.")
        return 0

    if limit_seasons is not None:
        filtered = filtered[:limit_seasons]

    if verbose:
        print(f"Found {len(filtered)} seasons to download.")

    combined_payload: dict[str, Any] = {
        "metadata": {
            "category_id": category_id,
            "category_name": category_name,
            "start_year": start_year,
            "limit_seasons": limit_seasons,
            "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "seasons": [],
    }

    total_failures = 0
    total_new_games = 0
    total_boxscores_fixed = 0

    for idx, season in enumerate(filtered, 1):
        season_name = season.get("season_name") or season.get("competition_id")
        competition_id = season.get("competition_id") or season.get("season_id")
        is_historical = _is_historical_season(season)

        existing_match_count = 0
        existing_boxscore_count = 0
        existing_payload = None

        if verbose and idx > 1:
            print(f"\n{'=' * 60}\n")

        if verbose:
            print(f"\n[{idx}/{len(filtered)}] Processing season {season_name}")

        if is_historical:
            output_file = (
                output_path / f"{safe_category}_{season_name}_baskethotel.json"
            )
            target_file = (
                output_path / f".tmp_{safe_category}_{season_name}_baskethotel.json"
                if combine_output
                else output_file
            )
            if output_file.exists():
                try:
                    existing_payload = json.loads(
                        output_file.read_text(encoding="utf-8")
                    )
                except Exception:
                    logging.getLogger(__name__).debug(
                        "league_boxscores: operation failed", exc_info=True
                    )
                    existing_payload = None
            download_baskethotel_season_boxscores(
                category_id=category_id,
                season_id=season_name or str(competition_id),
                output_file=str(target_file),
                max_workers=max_workers,
                show_header=False,
                verbose=verbose,
            )
        else:
            output_file = output_path / f"{safe_category}_{season_name}_genius.json"
            target_file = (
                output_path / f".tmp_{safe_category}_{season_name}_genius.json"
                if combine_output
                else output_file
            )
            if output_file.exists():
                try:
                    existing_payload = json.loads(
                        output_file.read_text(encoding="utf-8")
                    )
                except Exception:
                    logging.getLogger(__name__).debug(
                        "league_boxscores: operation failed", exc_info=True
                    )
                    existing_payload = None
            download_season_comprehensive(
                category_id=category_id,
                competition_id=str(competition_id or season_name),
                output_file=str(target_file),
                season_name=season_name,
                include_advanced=True,
                max_workers=max_workers,
                show_header=False,
                verbose=verbose,
            )

        if existing_payload:
            existing_matches = existing_payload.get("matches") or []
            if isinstance(existing_matches, list):
                existing_match_count = len(existing_matches)
                existing_boxscore_count = sum(
                    1 for m in existing_matches if m.get("boxscore")
                )

        try:
            current_payload = json.loads(target_file.read_text(encoding="utf-8"))
            metadata = current_payload.get("metadata", {})
            total_failures += max(
                int(metadata.get("matches_failed", 0)),
                int(metadata.get("conversion_failures", 0)),
            ) + int(metadata.get("teams_failed", 0))
            current_matches = current_payload.get("matches") or []
            current_match_count = (
                len(current_matches) if isinstance(current_matches, list) else 0
            )
            current_boxscore_count = (
                sum(1 for m in current_matches if m.get("boxscore"))
                if isinstance(current_matches, list)
                else 0
            )
            total_new_games += max(0, current_match_count - existing_match_count)
            total_boxscores_fixed += max(
                0, current_boxscore_count - existing_boxscore_count
            )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError(
                f"Season download produced invalid output: {target_file}"
            ) from exc

        if combine_output:
            try:
                season_payload = json.loads(target_file.read_text(encoding="utf-8"))
                season_payload["season_name"] = season_name
                season_payload["source"] = "baskethotel" if is_historical else "genius"
                combined_payload["seasons"].append(season_payload)
            finally:
                if target_file.exists():
                    target_file.unlink()

    if combine_output:
        combined_path = (
            Path(combined_file)
            if combined_file
            else output_path / f"{safe_category}_combined.json"
        )
        with open(combined_path, "w", encoding="utf-8") as f:
            json.dump(combined_payload, f, indent=2, ensure_ascii=False)
        if verbose:
            print(f"\nSaved combined output to {combined_path}")

    if verbose:
        print(f"\n{'=' * 60}")
        print("LEAGUE DOWNLOAD SUMMARY")
        print(f"{'=' * 60}")
        print(f"  - New games fetched: {total_new_games}")
        print(f"  - Boxscores fixed: {total_boxscores_fixed}")
        print(f"{'=' * 60}")

    return total_failures


def download_league_boxscores_playbyplay_all_seasons(
    category_id: str,
    output_dir: str,
    start_year: int = 2010,
    limit_seasons: int | None = None,
    include_playbyplay: bool = False,
    include_advanced: bool = False,
    include_shot_chart: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> BatchReport:
    """
    Download season boxscores (and optional play-by-play) for all seasons.

    Historical seasons (pre-2022) are fetched from BasketHotel.
    Modern seasons (2022+) use season-boxscores (Genius Sports).
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"\n{'=' * 80}")
        print("LEAGUE BOXSCORES + PLAY-BY-PLAY (ALL SEASONS)")
        print(f"{'=' * 80}")
        print(f"Category ID: {category_id}")
        print(f"Start year: {start_year}")
        print(f"Output directory: {output_path.absolute()}")
        print(f"{'=' * 80}\n")

    category_data = BasketFiAPI.get_category(current_season_id(), category_id)

    if "category" not in category_data or "seasons" not in category_data["category"]:
        raise ValueError(f"Could not retrieve seasons for category {category_id}")

    category = category_data["category"]
    category_name = category.get("category_name", str(category_id))
    safe_category = (
        category_name.lower().replace(" ", "_").replace("/", "_").replace("__", "_")
    )

    seasons = category["seasons"]
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)

    filtered = []
    for season in seasons:
        season_name = season.get("season_name")
        season_year = _extract_season_start_year(season_name)
        if season_year is None or season_year < start_year:
            continue
        filtered.append(season)

    if not filtered:
        print("No seasons found for the requested year range.")
        return BatchReport(())

    if limit_seasons is not None:
        filtered = filtered[:limit_seasons]

    if verbose:
        print(f"Found {len(filtered)} seasons to download.")

    outcomes: list[GameOutcome] = []
    for idx, season in enumerate(filtered, 1):
        season_name = season.get("season_name") or season.get("competition_id")
        competition_id = season.get("competition_id") or season.get("season_id")
        is_historical = _is_historical_season(season)

        if verbose and idx > 1:
            print(f"\n{'=' * 60}\n")

        if verbose:
            print(f"\n[{idx}/{len(filtered)}] Processing season {season_name}")

        if is_historical:
            output_file = (
                output_path / f"{safe_category}_{season_name}_baskethotel.json"
            )
            report = download_baskethotel_season_boxscores(
                category_id=category_id,
                season_id=season_name or str(competition_id),
                output_file=str(output_file),
                max_workers=max_workers,
                include_playbyplay=include_playbyplay,
                include_shot_chart=include_shot_chart,
                show_header=False,
                verbose=verbose,
            )
        else:
            output_file = output_path / f"{safe_category}_{season_name}_genius.json"
            report = download_matches_with_boxscores(
                season_id=str(competition_id or season_name),
                category_id=category_id,
                output_file=str(output_file),
                include_advanced=include_advanced,
                include_playbyplay=include_playbyplay,
                include_shot_chart=include_shot_chart,
                max_workers=max_workers,
                verbose=verbose,
            )

        outcomes.extend(report.outcomes)
    return BatchReport(tuple(outcomes))


def download_league_all_seasons(
    category_id: str,
    output_file: str,
    season_id: str | None = None,
    include_advanced: bool = False,
    max_workers: int = 5,
    verbose: bool = True,
) -> None:
    """Download all matches from all seasons for a specific league/category."""
    output_path = Path(output_file)

    if verbose:
        print(f"Fetching category information for category {category_id}...")

    # First, get category info to find all available seasons
    category_data = BasketFiAPI.get_category(
        season_id or current_season_id(), category_id
    )

    if "category" not in category_data or "seasons" not in category_data["category"]:
        print("Error: Could not retrieve seasons for this category.")
        return

    seasons = category_data["category"]["seasons"]
    seasons = _augment_seasons_with_baskethotel(seasons, category_id)
    category_name = category_data["category"].get("category_name", "Unknown")

    if not seasons:
        print("No seasons found for this category.")
        return

    if verbose:
        print(f"\nLeague: {category_name}")
        print(f"Found {len(seasons)} seasons to download")
        print(f"{'=' * 60}\n")

    # Collect all matches from all seasons
    all_matches: list[dict[str, Any]] = []
    total_matches_found = 0
    total_played_matches = 0
    total_advanced_stats = 0
    total_failed = 0
    seasons_processed = []

    for idx, season in enumerate(seasons, 1):
        season_data_id = season["season_id"]
        season_name = season["season_name"]
        season_competition_id = season["competition_id"]
        is_historical = _is_historical_season(season)

        if verbose:
            print(f"[{idx}/{len(seasons)}] Processing season: {season_name}")

        try:
            if is_historical:
                matches = []
            else:
                matches_data = BasketFiAPI.get_matches(
                    competition_id=season_competition_id, category_id=category_id
                )
                matches = BasketFiParser.extract_matches(matches_data)
                total_matches_found += len(matches)

            # Process matches for this season
            if is_historical:
                if verbose:
                    print("  - Using BasketHotel API for historical season data")
                processed_matches_raw = _fetch_historical_matches(
                    matches=matches,
                    season_name=season_name,
                    season_id=str(season_data_id) if season_data_id else None,
                    category_id=category_id,
                    category_name=category_name,
                    max_workers=max_workers,
                    verbose=verbose,
                )
                total_matches_found += len(processed_matches_raw)
            else:
                processed_matches_raw = BasketFiParser.parse_matches(
                    matches, season_name=season_name, only_played=True
                )
            # Add season_id to each match for league-comprehensive
            processed_matches = []
            for match_data in processed_matches_raw:
                match_data_with_season = {**match_data, "season_id": season_data_id}
                processed_matches.append(match_data_with_season)

            if include_advanced:
                processed_matches, _report = enrich_matches(
                    processed_matches,
                    (Component.BOXSCORE,),
                    season_reference(
                        category_id, season_name, historical=is_historical
                    ),
                    max_workers=max_workers,
                    cache_dir=output_path.parent
                    / ".cache"
                    / str(season_competition_id),
                )
                season_advanced = sum(
                    bool(row.get("boxscore")) for row in processed_matches
                )
                total_advanced_stats += season_advanced
                total_failed += len(processed_matches) - season_advanced
            total_played_matches += len(processed_matches)
            all_matches.extend(processed_matches)

            seasons_processed.append(
                {
                    "season_id": season_data_id,
                    "season_name": season_name,
                    "competition_id": season_competition_id,
                    "total_matches": len(processed_matches_raw)
                    if is_historical
                    else len(matches),
                    "played_matches": len(processed_matches),
                }
            )

        except Exception as e:
            logging.getLogger(__name__).debug(
                "league_boxscores: operation failed", exc_info=True
            )
            if verbose:
                print(f"  ✗ Error processing season {season_name}: {e!s}")
            continue

    # Save all matches to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "metadata": {
            "category_id": category_id,
            "category_name": category_name,
            "total_seasons": len(seasons),
            "seasons_processed": len(seasons_processed),
            "total_matches_found": total_matches_found,
            "total_played_matches_saved": total_played_matches,
            "matches_with_advanced_stats": total_advanced_stats,
            "matches_failed": total_failed,
            "include_advanced_stats": include_advanced,
        },
        "seasons": seasons_processed,
        "matches": all_matches,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n{'=' * 60}")
        print(
            f"✓ Successfully saved {total_played_matches} played matches to {output_path}"
        )
        print(f"  - League: {category_name}")
        print(f"  - Seasons processed: {len(seasons_processed)}/{len(seasons)}")
        print(f"  - Total matches found: {total_matches_found}")
        print(f"  - Played matches saved: {total_played_matches}")
        if include_advanced:
            print(f"  - Advanced stats: {total_advanced_stats} matches")
            if total_failed > 0:
                print(f"  - Failed: {total_failed}")
                # Count error types
                error_types: dict[str, int] = {}
                for match in all_matches:
                    if "advanced_boxscore_error" in match:
                        err_type = match["advanced_boxscore_error"].get(
                            "error_type", "Unknown"
                        )
                        error_types[err_type] = error_types.get(err_type, 0) + 1

                if error_types:
                    print("  - Error breakdown:")
                    for err_type, count in sorted(
                        error_types.items(), key=lambda x: -x[1]
                    ):
                        print(f"    • {err_type}: {count}")
        print(f"{'=' * 60}")
