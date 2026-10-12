"""Convert raw public widget responses, without legacy normalization."""

from collections.abc import Mapping

from ..data import fields, records, text
from .basketfi import from_basketfi
from .common import (
    STATUS,
    ConversionError,
    boolean,
    clock,
    count,
    external,
    finish,
    first,
    known,
    number,
    pb,
    player,
    put,
    stats,
    team_for,
)

EVENT_TYPES = {
    "2pt": pb.EVENT_TYPE_SHOT,
    "3pt": pb.EVENT_TYPE_SHOT,
    "freethrow": pb.EVENT_TYPE_SHOT,
    "rebound": pb.EVENT_TYPE_REBOUND,
    "assist": pb.EVENT_TYPE_ASSIST,
    "turnover": pb.EVENT_TYPE_TURNOVER,
    "steal": pb.EVENT_TYPE_STEAL,
    "block": pb.EVENT_TYPE_BLOCK,
    "foul": pb.EVENT_TYPE_FOUL,
    "substitution": pb.EVENT_TYPE_SUBSTITUTION,
    "timeout": pb.EVENT_TYPE_TIMEOUT,
    "jumpball": pb.EVENT_TYPE_JUMP_BALL,
    "gamestart": pb.EVENT_TYPE_GAME_START,
    "gameend": pb.EVENT_TYPE_GAME_END,
    "periodstart": pb.EVENT_TYPE_PERIOD_START,
    "periodend": pb.EVENT_TYPE_PERIOD_END,
}
ROLES = {
    pb.EVENT_TYPE_SHOT: "shooter",
    pb.EVENT_TYPE_ASSIST: "assister",
    pb.EVENT_TYPE_REBOUND: "rebounder",
    pb.EVENT_TYPE_STEAL: "stealer",
    pb.EVENT_TYPE_BLOCK: "blocker",
    pb.EVENT_TYPE_FOUL: "fouler",
}


def from_sportradar(
    *,
    boxscore: Mapping[str, object] | None = None,
    playbyplay: Mapping[str, object] | None = None,
    basketfi: Mapping[str, object] | None = None,
) -> pb.Game:
    """Accept raw JSON envelopes or their data objects. Inputs remain untouched."""
    payloads = [
        fields(p.get("data", p)) for p in (boxscore, playbyplay) if p is not None
    ]
    if not payloads:
        raise ConversionError("At least one Sportradar response is required")
    fixture = fields(payloads[0].get("fixture"))
    fixture_id = first(fixture, "fixtureId", "id")
    if not known(fixture_id):
        raise ConversionError("Sportradar fixtureId is required")
    if any(
        first(fields(p.get("fixture")), "fixtureId", "id") != fixture_id
        for p in payloads
    ):
        raise ConversionError("Cannot combine responses for different fixtures")
    if basketfi is not None:
        row = fields(basketfi.get("match", basketfi))
        if str(row.get("match_external_id")) != str(fixture_id):
            raise ConversionError("Basket.fi and Sportradar fixture IDs disagree")
        game = from_basketfi(basketfi)
    else:
        game = pb.Game(id=f"sportradar:fixture:{fixture_id}")
        external(game, "sportradar.fixture", fixture_id)
    game.status = STATUS.get(str(fixture.get("status", "")).lower(), game.status)
    start = text(fixture.get("startTimeLocal"))
    if start is not None:
        put(game, "date", start[:10])
        put(game, "time", start[11:16] or None)
    banner = fields(payloads[0].get("banner"))
    put(
        game,
        "league",
        first(fixture, "competitionName")
        or fields(banner.get("competition")).get("name"),
    )
    put(game, "season", fields(banner.get("season")).get("name"))
    teams: dict[str, tuple[str, pb.Team]] = {}
    for competitor in records(fixture.get("competitors")):
        home = competitor.get("isHome")
        if not isinstance(home, bool):
            raise ConversionError("Sportradar competitor missing home/away role")
        side = "home" if home else "away"
        team = team_for(game, side)
        put(team, "name", competitor.get("name"))
        put(team, "score", count(competitor.get("score")))
        external(team, "sportradar.entity", competitor.get("entityId"))
        teams[str(competitor["entityId"])] = (side, team)
    if len(teams) != 2 or len({v[0] for v in teams.values()}) != 2:
        raise ConversionError("Sportradar requires one home and one away competitor")

    if boxscore is not None:
        data = fields(boxscore.get("data", boxscore))
        base = fields(fields(data.get("statistics")).get("data")).get("base")
        base = fields(base)
        if not base:
            raise ConversionError("Sportradar response missing box score")
        for side in ("home", "away"):
            team = team_for(game, side)
            raw = fields(base.get(side))
            if raw.get("entity") is not None:
                team.totals.CopyFrom(stats(fields(raw["entity"])))
            for group in records(raw.get("persons")):
                for row in records(group.get("rows")):
                    p = player(
                        team,
                        native_id=row.get("personId"),
                        name=text(row.get("personName")),
                        jersey=row.get("bib"),
                        namespace="sportradar.person",
                        scope=f"{game.id}:{side}",
                    )
                    values = {
                        **fields(row.get("statistics")),
                        "starter": row.get("starter"),
                        "participated": row.get("participated"),
                    }
                    p.stats.CopyFrom(stats(values, participation=True))

    if playbyplay is not None:
        data = fields(playbyplay.get("data", playbyplay))
        if not isinstance(data.get("periodData"), list):
            raise ConversionError("Sportradar response missing periods")
        ids: set[str] = set()
        for period in sorted(
            records(data["periodData"]), key=lambda p: count(p["periodId"]) or 0
        ):
            for index, raw in enumerate(records(period.get("events"))):
                event_id = str(
                    raw.get("eventId")
                    or f"sportradar:period:{period['periodId']}:row:{index}"
                )
                if event_id in ids:
                    raise ConversionError(f"Duplicate event ID: {event_id}")
                ids.add(event_id)
                kind = str(raw.get("eventType") or "").lower()
                event = game.events.add(
                    id=event_id, type=EVENT_TYPES.get(kind, pb.EVENT_TYPE_OTHER)
                )
                put(event, "period", count(raw.get("periodId", period["periodId"])))
                put(event, "game_clock", clock(raw.get("clock")))
                put(event, "detail", raw.get("eventSubType"))
                put(event, "description", raw.get("desc") or raw.get("eventType"))
                scores = fields(raw.get("scores"))
                for entity_id, (side, team) in teams.items():
                    put(event, f"{side}_score", count(scores.get(entity_id)))
                resolved = teams.get(str(raw.get("entityId")))
                p = None
                if resolved:
                    side, team = resolved
                    event.home = side == "home"
                    if known(raw.get("personId")) or known(raw.get("name")):
                        p = player(
                            team,
                            native_id=raw.get("personId"),
                            name=text(raw.get("name")),
                            jersey=raw.get("bib"),
                            namespace="sportradar.person",
                            scope=f"{game.id}:{side}",
                        )
                        participant = event.players.add(player_id=p.id)
                        role = ROLES.get(event.type)
                        if event.type == pb.EVENT_TYPE_SUBSTITUTION:
                            role = {"in": "incoming", "out": "outgoing"}.get(
                                str(raw.get("eventSubType"))
                            )
                        if (
                            event.type == pb.EVENT_TYPE_FOUL
                            and raw.get("eventSubType") == "drawn"
                        ):
                            role = "fouled"
                        put(participant, "role", role)
                if event.type == pb.EVENT_TYPE_SHOT:
                    event.point_value = {"2pt": 2, "3pt": 3, "freethrow": 1}[kind]
                    put(event, "made", boolean(raw.get("success")))
                    if p is not None:
                        shot = p.shots.add(
                            event_id=event.id,
                            period=event.period,
                            point_value=event.point_value,
                        )
                        if event.HasField("made"):
                            shot.made = event.made
                        put(shot, "game_clock", clock(raw.get("clock")))
                        put(shot, "shot_type", raw.get("eventSubType"))
                        put(shot, "x", number(raw.get("x")))
                        put(shot, "y", number(raw.get("y")))
                        external(shot, "sportradar.event", raw.get("eventId"), game.id)
    return finish(game)
