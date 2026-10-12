"""Pure projections of protobuf games to nullable analysis tables."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeAlias, cast

import pandas as pd
from google.protobuf.message import Message

from koris_api import SeasonData
from koris_api.converters import validate_game
from koris_api.schema.koris.v1 import game_pb2 as pb

Scalar: TypeAlias = str | int | float | bool | None
Row: TypeAlias = dict[str, Scalar]
STAT_FIELDS = (
    "points",
    "two_pointers_made",
    "two_pointers_attempted",
    "three_pointers_made",
    "three_pointers_attempted",
    "free_throws_made",
    "free_throws_attempted",
    "offensive_rebounds",
    "defensive_rebounds",
    "rebounds",
    "assists",
    "steals",
    "turnovers",
    "blocks",
    "personal_fouls",
    "played",
    "seconds_played",
    "starter",
)
CONTEXT = ("game_key", "game_id", "category_id", "league", "season", "provider", "date")
TEAM_CONTEXT = (*CONTEXT, "home", "team", "opponent", "boxscore_valid")


@dataclass(frozen=True)
class AnalysisTables:
    games: pd.DataFrame
    teams: pd.DataFrame
    players: pd.DataFrame
    shots: pd.DataFrame
    events: pd.DataFrame
    issues: pd.DataFrame


def _optional(message: Message, field: str) -> Scalar:
    return cast(Scalar, getattr(message, field)) if message.HasField(field) else None


def _stats(stats: pb.Stats) -> Row:
    return {field: _optional(stats, field) for field in STAT_FIELDS}


def _provider(game: pb.Game) -> str:
    namespaces = {identifier.namespace for identifier in game.external_ids}
    for namespace, provider in (
        ("sportradar.fixture", "sportradar"),
        ("genius.match", "genius"),
        ("baskethotel.game", "baskethotel"),
    ):
        if namespace in namespaces:
            return provider
    return game.id.split(":", 1)[0]


def _frame(rows: list[Row], columns: tuple[str, ...]) -> pd.DataFrame:
    # Explicit columns make empty datasets usable without notebook special cases.
    return pd.DataFrame(rows, columns=list(columns)).convert_dtypes()


def to_frames(datasets: Iterable[SeasonData]) -> AnalysisTables:
    """Preserve absent facts as NA, source IDs, scope, and quality flags.

    Player IDs may be source/game scoped; never infer cross-season identity from
    names. Coordinates remain in each provider's reported coordinate system.
    Games with incomplete box scores remain visible in games/issues, and carry
    ``boxscore_valid=False`` for statistical filtering.
    """
    games: list[Row] = []
    teams: list[Row] = []
    players: list[Row] = []
    shots: list[Row] = []
    events: list[Row] = []
    issues: list[Row] = []
    for dataset in datasets:
        season = dataset.season
        failed_ids = {issue.game_id for issue in dataset.issues}
        for issue in dataset.issues:
            issues.extend(
                {
                    "category_id": season.category_id,
                    "season": season.name,
                    "game_id": issue.game_id,
                    "source": issue.source,
                    "message": message,
                }
                for message in issue.messages
            )
        for game in dataset.games:
            context: Row = {
                "game_key": f"{season.category_id}:{season.id}:{game.id}",
                "game_id": game.id,
                "category_id": season.category_id,
                "league": season.league,
                "season": season.name,
                "provider": _provider(game),
                "date": _optional(game, "date"),
            }
            valid = not validate_game(game)
            has_box = bool(game.home.players and game.away.players)
            box_valid = (
                has_box
                and valid
                and all(
                    team.totals.HasField("points")
                    and sum(
                        player.stats.points
                        for player in team.players
                        if player.stats.HasField("points")
                    )
                    == team.totals.points
                    for team in (game.home, game.away)
                )
            )
            games.append(
                {
                    **context,
                    "home_team": _optional(game.home, "name"),
                    "away_team": _optional(game.away, "name"),
                    "home_score": _optional(game.home, "score"),
                    "away_score": _optional(game.away, "score"),
                    "status": pb.GameStatus.Name(game.status),
                    "complete": game.id not in failed_ids,
                    "valid": valid,
                    "boxscore_available": has_box,
                    "boxscore_valid": box_valid,
                    "event_count": len(game.events),
                    "shot_count": sum(
                        len(p.shots) for t in (game.home, game.away) for p in t.players
                    ),
                }
            )
            for home, team, opponent in (
                (True, game.home, game.away),
                (False, game.away, game.home),
            ):
                team_context: Row = {
                    **context,
                    "home": home,
                    "team": _optional(team, "name"),
                    "opponent": _optional(opponent, "name"),
                    "boxscore_valid": box_valid,
                }
                teams.append(
                    {
                        **team_context,
                        "score": _optional(team, "score"),
                        "opponent_score": _optional(opponent, "score"),
                        "win": _optional(team, "win"),
                        **_stats(team.totals),
                    }
                )
                for player in team.players:
                    player_context: Row = {
                        **team_context,
                        "player_id": _optional(player, "id"),
                        "player": _optional(player, "name"),
                    }
                    players.append(
                        {
                            **player_context,
                            "jersey_number": _optional(player, "jersey_number"),
                            **_stats(player.stats),
                        }
                    )
                    shots.extend(
                        {
                            **player_context,
                            **{
                                field: _optional(shot, field)
                                for field in (
                                    "period",
                                    "game_clock",
                                    "point_value",
                                    "made",
                                    "x",
                                    "y",
                                    "event_id",
                                )
                            },
                        }
                        for shot in player.shots
                    )
            events.extend(
                {
                    **context,
                    "event_id": event.id,
                    "type": pb.EventType.Name(event.type),
                    **{
                        field: _optional(event, field)
                        for field in (
                            "period",
                            "game_clock",
                            "home",
                            "home_score",
                            "away_score",
                            "point_value",
                            "made",
                            "detail",
                            "description",
                        )
                    },
                    "player_count": len(event.players),
                }
                for event in game.events
            )
    return AnalysisTables(
        _frame(
            games,
            (
                *CONTEXT,
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "status",
                "complete",
                "valid",
                "boxscore_available",
                "boxscore_valid",
                "event_count",
                "shot_count",
            ),
        ),
        _frame(teams, (*TEAM_CONTEXT, "score", "opponent_score", "win", *STAT_FIELDS)),
        _frame(
            players,
            (*TEAM_CONTEXT, "player_id", "player", "jersey_number", *STAT_FIELDS),
        ),
        _frame(
            shots,
            (
                *TEAM_CONTEXT,
                "player_id",
                "player",
                "period",
                "game_clock",
                "point_value",
                "made",
                "x",
                "y",
                "event_id",
            ),
        ),
        _frame(
            events,
            (
                *CONTEXT,
                "event_id",
                "type",
                "period",
                "game_clock",
                "home",
                "home_score",
                "away_score",
                "point_value",
                "made",
                "detail",
                "description",
                "player_count",
            ),
        ),
        _frame(issues, ("category_id", "season", "game_id", "source", "message")),
    )
