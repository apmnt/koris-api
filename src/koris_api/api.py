"""Typed, in-memory access to source data in the koris.v1 format."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from google.protobuf.json_format import MessageToDict, ParseDict

from .basketfi_api import BasketFiAPI
from .data import fields, records
from .pipeline import Component, GameOutcome, GameRef, fetch_game, resolve_reference
from .pipeline.export import write_json
from .schema.koris.v1.game_pb2 import Game
from .seasons import current_season_id
from .services.canonical_games import fetch_canonical_season
from .services.common import available_season_rows


@dataclass(frozen=True)
class Season:
    category_id: str
    id: str
    name: str
    league: str

    @property
    def historical(self) -> bool:
        return self.name[:4].isdigit() and int(self.name[:4]) <= 2023


@dataclass(frozen=True)
class DataIssue:
    game_id: str
    source: str
    messages: tuple[str, ...]


@dataclass(frozen=True)
class SeasonData:
    season: Season
    games: tuple[Game, ...]
    issues: tuple[DataIssue, ...]
    requested_games: int
    components: tuple[Component, ...]
    fetched_at: str
    limit_games: int | None = None

    @property
    def complete(self) -> bool:
        return not self.issues and len(self.games) == self.requested_games


def list_seasons(
    category_id: str = "4", *, competition_id: str | None = None
) -> tuple[Season, ...]:
    """Discover seasons from Basket.fi and the supported BasketHotel archives.

    Category 4 is Korisliiga, category 2 is men's division A. Newest first.
    Discovery performs I/O; it never downloads game statistics.
    """
    category = fields(
        BasketFiAPI.get_category(competition_id or current_season_id(), category_id)[
            "category"
        ]
    )
    league = str(category.get("category_name") or category_id)
    rows: object = available_season_rows(
        [dict(row) for row in records(category.get("seasons"))], category_id
    )
    result: dict[str, Season] = {}
    for row in records(rows):
        name = str(row.get("season_name") or row.get("season_id") or "")
        identifier = str(row.get("competition_id") or name)
        if not identifier or not name:
            raise ValueError("Source season has no identifier or name")
        result.setdefault(name, Season(category_id, identifier, name, league))
    return tuple(sorted(result.values(), key=lambda season: season.name, reverse=True))


def get_game(
    source: str | GameRef,
    *,
    components: Sequence[Component] = tuple(Component),
    cache_dir: Path | None = None,
) -> GameOutcome:
    """Fetch one source game. Pass an explicit GameRef for BasketHotel context.

    Numeric IDs resolve to Genius LiveStats and UUIDs to Sportradar. The result
    retains partial data and diagnostics; inspect ``complete`` before analysis.
    """
    reference = resolve_reference(source) if isinstance(source, str) else source
    return fetch_game(reference, tuple(components), cache_dir=cache_dir)


def get_season(
    season: Season,
    *,
    components: Sequence[Component] = (Component.BOXSCORE, Component.PLAYBYPLAY),
    cache_dir: Path | None = None,
    limit_games: int | None = None,
    max_workers: int = 5,
) -> SeasonData:
    """Fetch played games, retaining source facts and explicit data issues.

    No exports are written. Optional raw checkpoints speed repeated acquisition.
    Add Component.SHOTS for modern sources' dedicated chart geometry. Historical
    play-by-play already produces player shots; dedicated BasketHotel charts are
    unsupported. ``limit_games`` selects the latest games where dates exist.
    """
    if limit_games is not None and limit_games < 0:
        raise ValueError("limit_games must be nonnegative")
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    requested = tuple(dict.fromkeys(components))
    report = fetch_canonical_season(
        season.id,
        season.category_id,
        requested,
        cache_dir=cache_dir,
        limit_games=limit_games,
        max_workers=max_workers,
    )
    games: list[Game] = []
    issues: list[DataIssue] = []
    for outcome in report.outcomes:
        if outcome.game is not None:
            # Protobuf objects are mutable: copy before attaching discovery context.
            game = Game()
            game.CopyFrom(outcome.game)
            game.season = season.name
            game.league = season.league
            games.append(game)
        if not outcome.complete:
            messages = (
                *([outcome.conversion_failure] if outcome.conversion_failure else []),
                *outcome.issues,
                *[
                    f"{fetch.request.name}: {fetch.failure.message}"
                    for fetch in outcome.fetches
                    if fetch.failure is not None
                ],
            )
            issues.append(
                DataIssue(
                    outcome.game.id
                    if outcome.game is not None
                    else outcome.reference.key,
                    outcome.reference.key,
                    messages or ("No converted game available",),
                )
            )
    return SeasonData(
        season,
        tuple(games),
        tuple(issues),
        len(report.outcomes),
        requested,
        datetime.now(timezone.utc).isoformat(),
        limit_games,
    )


def save_season(path: Path, data: SeasonData) -> None:
    """Atomically save a portable snapshot, including request scope and failures."""
    write_json(
        path,
        {
            "format": "koris.season.v1",
            "season": {
                "category_id": data.season.category_id,
                "id": data.season.id,
                "name": data.season.name,
                "league": data.season.league,
            },
            "requested_games": data.requested_games,
            "components": [component.value for component in data.components],
            "fetched_at": data.fetched_at,
            "limit_games": data.limit_games,
            "issues": [
                {"game_id": i.game_id, "source": i.source, "messages": i.messages}
                for i in data.issues
            ],
            "games": [
                MessageToDict(game, preserving_proto_field_name=True)
                for game in data.games
            ],
        },
    )


def load_season(path: Path) -> SeasonData:
    """Read a snapshot without network access. Reject unknown snapshot versions."""
    raw = fields(cast(object, json.loads(path.read_text(encoding="utf-8"))))
    if raw.get("format") != "koris.season.v1":
        raise ValueError("Unsupported season snapshot format")
    season = fields(raw["season"])
    requested_games = raw["requested_games"]
    if (
        isinstance(requested_games, bool)
        or not isinstance(requested_games, int)
        or requested_games < 0
    ):
        raise ValueError("requested_games must be a nonnegative integer")
    limit_games = raw.get("limit_games")
    if limit_games is not None and (
        isinstance(limit_games, bool)
        or not isinstance(limit_games, int)
        or limit_games < 0
    ):
        raise ValueError("limit_games must be a nonnegative integer or null")
    games = tuple(ParseDict(dict(row), Game()) for row in records(raw["games"]))
    if len(games) > requested_games or len({game.id for game in games}) != len(games):
        raise ValueError("Snapshot has inconsistent game counts or duplicate game IDs")
    return SeasonData(
        Season(
            *(_string(season[key]) for key in ("category_id", "id", "name", "league"))
        ),
        games,
        tuple(
            DataIssue(
                _string(row["game_id"]),
                _string(row["source"]),
                _strings(row["messages"]),
            )
            for row in records(raw["issues"])
        ),
        requested_games,
        tuple(Component(value) for value in _strings(raw["components"])),
        _string(raw["fetched_at"]),
        limit_games,
    )


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected snapshot text")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("Expected an array of strings")
    values = cast(list[object], value)
    if any(not isinstance(item, str) for item in values):
        raise TypeError("Expected an array of strings")
    return cast(tuple[str, ...], tuple(values))
