"""Small immutable plans and outcomes; game facts belong in koris.v1."""

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from ..schema.koris.v1.game_pb2 import Game

JSON: TypeAlias = None | bool | int | float | str | list["JSON"] | dict[str, "JSON"]
JSONObject: TypeAlias = dict[str, JSON]


class Provider(str, Enum):
    UNRESOLVED = "unresolved"
    SPORTRADAR = "sportradar"
    GENIUS = "genius"
    GENIUS_HTML = "genius_html"
    BASKETHOTEL = "baskethotel"


class Component(str, Enum):
    BOXSCORE = "boxscore"
    PLAYBYPLAY = "playbyplay"
    SHOTS = "shot_chart"


@dataclass(frozen=True)
class GameRef:
    provider: Provider
    source_id: str
    competition_id: str | None = None
    league_id: str | None = None
    season_id: str | None = None
    regulation_periods: int = 4
    clock_mode: str | None = None

    @property
    def key(self) -> str:
        return f"{self.provider.value}:{self.league_id or ''}:{self.season_id or ''}:{self.source_id}"


@dataclass(frozen=True)
class Request:
    name: str
    url: str
    components: tuple[Component, ...]
    json: bool = True


@dataclass(frozen=True)
class FetchFailure:
    kind: str
    message: str
    url: str | None = None
    status_code: int | None = None
    retryable: bool = False


@dataclass(frozen=True)
class FetchOutcome:
    request: Request
    payload: JSONObject | str | None = None
    failure: FetchFailure | None = None


@dataclass(frozen=True)
class GameOutcome:
    reference: GameRef
    requested: tuple[Component, ...]
    fetches: tuple[FetchOutcome, ...]
    game: Game | None = None
    conversion_failure: str | None = None
    issues: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return (
            self.game is not None
            and self.conversion_failure is None
            and not self.issues
            and all(fetch.failure is None for fetch in self.fetches)
        )


@dataclass(frozen=True)
class BatchReport:
    outcomes: tuple[GameOutcome, ...]

    @property
    def failed(self) -> int:
        return sum(not outcome.complete for outcome in self.outcomes)
