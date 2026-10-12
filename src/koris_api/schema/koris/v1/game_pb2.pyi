from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GameStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GAME_STATUS_UNSPECIFIED: _ClassVar[GameStatus]
    GAME_STATUS_SCHEDULED: _ClassVar[GameStatus]
    GAME_STATUS_IN_PROGRESS: _ClassVar[GameStatus]
    GAME_STATUS_FINAL: _ClassVar[GameStatus]
    GAME_STATUS_POSTPONED: _ClassVar[GameStatus]
    GAME_STATUS_CANCELLED: _ClassVar[GameStatus]

class EventType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EVENT_TYPE_UNSPECIFIED: _ClassVar[EventType]
    EVENT_TYPE_OTHER: _ClassVar[EventType]
    EVENT_TYPE_SHOT: _ClassVar[EventType]
    EVENT_TYPE_REBOUND: _ClassVar[EventType]
    EVENT_TYPE_ASSIST: _ClassVar[EventType]
    EVENT_TYPE_TURNOVER: _ClassVar[EventType]
    EVENT_TYPE_STEAL: _ClassVar[EventType]
    EVENT_TYPE_BLOCK: _ClassVar[EventType]
    EVENT_TYPE_FOUL: _ClassVar[EventType]
    EVENT_TYPE_SUBSTITUTION: _ClassVar[EventType]
    EVENT_TYPE_TIMEOUT: _ClassVar[EventType]
    EVENT_TYPE_JUMP_BALL: _ClassVar[EventType]
    EVENT_TYPE_GAME_START: _ClassVar[EventType]
    EVENT_TYPE_GAME_END: _ClassVar[EventType]
    EVENT_TYPE_PERIOD_START: _ClassVar[EventType]
    EVENT_TYPE_PERIOD_END: _ClassVar[EventType]
GAME_STATUS_UNSPECIFIED: GameStatus
GAME_STATUS_SCHEDULED: GameStatus
GAME_STATUS_IN_PROGRESS: GameStatus
GAME_STATUS_FINAL: GameStatus
GAME_STATUS_POSTPONED: GameStatus
GAME_STATUS_CANCELLED: GameStatus
EVENT_TYPE_UNSPECIFIED: EventType
EVENT_TYPE_OTHER: EventType
EVENT_TYPE_SHOT: EventType
EVENT_TYPE_REBOUND: EventType
EVENT_TYPE_ASSIST: EventType
EVENT_TYPE_TURNOVER: EventType
EVENT_TYPE_STEAL: EventType
EVENT_TYPE_BLOCK: EventType
EVENT_TYPE_FOUL: EventType
EVENT_TYPE_SUBSTITUTION: EventType
EVENT_TYPE_TIMEOUT: EventType
EVENT_TYPE_JUMP_BALL: EventType
EVENT_TYPE_GAME_START: EventType
EVENT_TYPE_GAME_END: EventType
EVENT_TYPE_PERIOD_START: EventType
EVENT_TYPE_PERIOD_END: EventType

class Game(_message.Message):
    __slots__ = ("id", "external_ids", "home", "away", "date", "time", "status", "league", "season", "events")
    ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_IDS_FIELD_NUMBER: _ClassVar[int]
    HOME_FIELD_NUMBER: _ClassVar[int]
    AWAY_FIELD_NUMBER: _ClassVar[int]
    DATE_FIELD_NUMBER: _ClassVar[int]
    TIME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LEAGUE_FIELD_NUMBER: _ClassVar[int]
    SEASON_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    id: str
    external_ids: _containers.RepeatedCompositeFieldContainer[ExternalId]
    home: Team
    away: Team
    date: str
    time: str
    status: GameStatus
    league: str
    season: str
    events: _containers.RepeatedCompositeFieldContainer[Event]
    def __init__(self, id: _Optional[str] = ..., external_ids: _Optional[_Iterable[_Union[ExternalId, _Mapping]]] = ..., home: _Optional[_Union[Team, _Mapping]] = ..., away: _Optional[_Union[Team, _Mapping]] = ..., date: _Optional[str] = ..., time: _Optional[str] = ..., status: _Optional[_Union[GameStatus, str]] = ..., league: _Optional[str] = ..., season: _Optional[str] = ..., events: _Optional[_Iterable[_Union[Event, _Mapping]]] = ...) -> None: ...

class Team(_message.Message):
    __slots__ = ("name", "external_ids", "players", "totals", "score", "win")
    NAME_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_IDS_FIELD_NUMBER: _ClassVar[int]
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    TOTALS_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    WIN_FIELD_NUMBER: _ClassVar[int]
    name: str
    external_ids: _containers.RepeatedCompositeFieldContainer[ExternalId]
    players: _containers.RepeatedCompositeFieldContainer[Player]
    totals: Stats
    score: int
    win: bool
    def __init__(self, name: _Optional[str] = ..., external_ids: _Optional[_Iterable[_Union[ExternalId, _Mapping]]] = ..., players: _Optional[_Iterable[_Union[Player, _Mapping]]] = ..., totals: _Optional[_Union[Stats, _Mapping]] = ..., score: _Optional[int] = ..., win: _Optional[bool] = ...) -> None: ...

class Shot(_message.Message):
    __slots__ = ("period", "point_value", "made", "x", "y", "shot_type", "external_ids", "game_clock", "event_id")
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    POINT_VALUE_FIELD_NUMBER: _ClassVar[int]
    MADE_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    SHOT_TYPE_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_IDS_FIELD_NUMBER: _ClassVar[int]
    GAME_CLOCK_FIELD_NUMBER: _ClassVar[int]
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    period: int
    point_value: int
    made: bool
    x: float
    y: float
    shot_type: str
    external_ids: _containers.RepeatedCompositeFieldContainer[ExternalId]
    game_clock: str
    event_id: str
    def __init__(self, period: _Optional[int] = ..., point_value: _Optional[int] = ..., made: _Optional[bool] = ..., x: _Optional[float] = ..., y: _Optional[float] = ..., shot_type: _Optional[str] = ..., external_ids: _Optional[_Iterable[_Union[ExternalId, _Mapping]]] = ..., game_clock: _Optional[str] = ..., event_id: _Optional[str] = ...) -> None: ...

class Player(_message.Message):
    __slots__ = ("name", "external_ids", "jersey_number", "stats", "shots", "id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_IDS_FIELD_NUMBER: _ClassVar[int]
    JERSEY_NUMBER_FIELD_NUMBER: _ClassVar[int]
    STATS_FIELD_NUMBER: _ClassVar[int]
    SHOTS_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    external_ids: _containers.RepeatedCompositeFieldContainer[ExternalId]
    jersey_number: str
    stats: Stats
    shots: _containers.RepeatedCompositeFieldContainer[Shot]
    id: str
    def __init__(self, name: _Optional[str] = ..., external_ids: _Optional[_Iterable[_Union[ExternalId, _Mapping]]] = ..., jersey_number: _Optional[str] = ..., stats: _Optional[_Union[Stats, _Mapping]] = ..., shots: _Optional[_Iterable[_Union[Shot, _Mapping]]] = ..., id: _Optional[str] = ...) -> None: ...

class Stats(_message.Message):
    __slots__ = ("points", "two_pointers_made", "two_pointers_attempted", "three_pointers_made", "three_pointers_attempted", "free_throws_made", "free_throws_attempted", "offensive_rebounds", "defensive_rebounds", "rebounds", "assists", "steals", "turnovers", "blocks", "personal_fouls", "played", "seconds_played", "starter")
    POINTS_FIELD_NUMBER: _ClassVar[int]
    TWO_POINTERS_MADE_FIELD_NUMBER: _ClassVar[int]
    TWO_POINTERS_ATTEMPTED_FIELD_NUMBER: _ClassVar[int]
    THREE_POINTERS_MADE_FIELD_NUMBER: _ClassVar[int]
    THREE_POINTERS_ATTEMPTED_FIELD_NUMBER: _ClassVar[int]
    FREE_THROWS_MADE_FIELD_NUMBER: _ClassVar[int]
    FREE_THROWS_ATTEMPTED_FIELD_NUMBER: _ClassVar[int]
    OFFENSIVE_REBOUNDS_FIELD_NUMBER: _ClassVar[int]
    DEFENSIVE_REBOUNDS_FIELD_NUMBER: _ClassVar[int]
    REBOUNDS_FIELD_NUMBER: _ClassVar[int]
    ASSISTS_FIELD_NUMBER: _ClassVar[int]
    STEALS_FIELD_NUMBER: _ClassVar[int]
    TURNOVERS_FIELD_NUMBER: _ClassVar[int]
    BLOCKS_FIELD_NUMBER: _ClassVar[int]
    PERSONAL_FOULS_FIELD_NUMBER: _ClassVar[int]
    PLAYED_FIELD_NUMBER: _ClassVar[int]
    SECONDS_PLAYED_FIELD_NUMBER: _ClassVar[int]
    STARTER_FIELD_NUMBER: _ClassVar[int]
    points: int
    two_pointers_made: int
    two_pointers_attempted: int
    three_pointers_made: int
    three_pointers_attempted: int
    free_throws_made: int
    free_throws_attempted: int
    offensive_rebounds: int
    defensive_rebounds: int
    rebounds: int
    assists: int
    steals: int
    turnovers: int
    blocks: int
    personal_fouls: int
    played: bool
    seconds_played: float
    starter: bool
    def __init__(self, points: _Optional[int] = ..., two_pointers_made: _Optional[int] = ..., two_pointers_attempted: _Optional[int] = ..., three_pointers_made: _Optional[int] = ..., three_pointers_attempted: _Optional[int] = ..., free_throws_made: _Optional[int] = ..., free_throws_attempted: _Optional[int] = ..., offensive_rebounds: _Optional[int] = ..., defensive_rebounds: _Optional[int] = ..., rebounds: _Optional[int] = ..., assists: _Optional[int] = ..., steals: _Optional[int] = ..., turnovers: _Optional[int] = ..., blocks: _Optional[int] = ..., personal_fouls: _Optional[int] = ..., played: _Optional[bool] = ..., seconds_played: _Optional[float] = ..., starter: _Optional[bool] = ...) -> None: ...

class Event(_message.Message):
    __slots__ = ("id", "type", "period", "game_clock", "home", "players", "home_score", "away_score", "point_value", "made", "detail", "description")
    ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    GAME_CLOCK_FIELD_NUMBER: _ClassVar[int]
    HOME_FIELD_NUMBER: _ClassVar[int]
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    HOME_SCORE_FIELD_NUMBER: _ClassVar[int]
    AWAY_SCORE_FIELD_NUMBER: _ClassVar[int]
    POINT_VALUE_FIELD_NUMBER: _ClassVar[int]
    MADE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    id: str
    type: EventType
    period: int
    game_clock: str
    home: bool
    players: _containers.RepeatedCompositeFieldContainer[EventPlayer]
    home_score: int
    away_score: int
    point_value: int
    made: bool
    detail: str
    description: str
    def __init__(self, id: _Optional[str] = ..., type: _Optional[_Union[EventType, str]] = ..., period: _Optional[int] = ..., game_clock: _Optional[str] = ..., home: _Optional[bool] = ..., players: _Optional[_Iterable[_Union[EventPlayer, _Mapping]]] = ..., home_score: _Optional[int] = ..., away_score: _Optional[int] = ..., point_value: _Optional[int] = ..., made: _Optional[bool] = ..., detail: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class EventPlayer(_message.Message):
    __slots__ = ("player_id", "role")
    PLAYER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    player_id: str
    role: str
    def __init__(self, player_id: _Optional[str] = ..., role: _Optional[str] = ...) -> None: ...

class ExternalId(_message.Message):
    __slots__ = ("namespace", "value", "scope")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    value: str
    scope: str
    def __init__(self, namespace: _Optional[str] = ..., value: _Optional[str] = ..., scope: _Optional[str] = ...) -> None: ...
