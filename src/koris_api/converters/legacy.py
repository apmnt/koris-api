"""Genius HTML/LiveStats and BasketHotel response adapters."""

import re
from collections.abc import Mapping
from datetime import datetime

from ..baskethotel_parser import BasketHotelParser
from ..data import fields, records, text
from ..genius_parser import GeniusSportsParser
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
from .sportradar import EVENT_TYPES, ROLES


def _period(value: object, regulation_periods: int | None) -> int | None:
    if not known(value):
        return None
    if str(value).lower() == "reg":
        return None
    match = re.fullmatch(r"(?:[PQ])?(\d+)", str(value), re.IGNORECASE)
    if match:
        return count(match[1]) or None
    match = re.fullmatch(r"OT(\d+)", str(value), re.IGNORECASE)
    if match and regulation_periods is not None:
        return regulation_periods + (count(match[1]) or 0)
    raise ConversionError(f"Period requires explicit regulation context: {value!r}")


def base_game(
    source: str,
    match_id: str,
    info: Mapping[str, object],
    basketfi: Mapping[str, object] | None = None,
    scope: str | None = None,
) -> pb.Game:
    if basketfi is not None:
        game = from_basketfi(basketfi)
        expected = fields(basketfi.get("match", basketfi)).get("match_external_id")
        if source == "genius" and known(expected) and str(expected) != str(match_id):
            raise ConversionError("Basket.fi and Genius match IDs disagree")
    else:
        game = pb.Game(id=f"{source}:{scope + ':' if scope else ''}game:{match_id}")
        external(
            game,
            f"{source}.{'match' if source == 'genius' else 'game'}",
            match_id,
            scope,
        )
    for side in ("home", "away"):
        team = team_for(game, side)
        put(team, "name", info.get(f"{side}_team"))
        put(team, "score", count(info.get(f"{side}_score")))
    game.status = STATUS.get(str(info.get("status", "")).lower(), game.status)
    return game


def _box(game: pb.Game, box: Mapping[str, object], source: str) -> dict[str, str]:
    rows = records(box.get("teams"))
    aliases: dict[str, str] = {}
    for raw in rows:
        for side in ("home", "away"):
            if raw.get("team_name") == team_for(game, side).name:
                aliases[str(raw["team_name"])] = side
    # Old widgets use abbreviated names. A unique reported point total can
    # identify the team against the header score without relying on row order.
    if source == "baskethotel" and len(rows) == 2:
        for raw in rows:
            name = str(raw.get("team_name"))
            if name in aliases:
                continue
            points = count(first(fields(raw.get("totals")), "PIST", "Points", "points"))
            candidates = [
                side
                for side in ("home", "away")
                if side not in aliases.values()
                and points is not None
                and team_for(game, side).HasField("score")
                and team_for(game, side).score == points
            ]
            if len(candidates) == 1:
                aliases[name] = candidates[0]
    # An exact counterpart resolves a single abbreviated name by elimination.
    if source == "baskethotel" and len(rows) == 2 and len(set(aliases.values())) == 1:
        remaining = ({"home", "away"} - set(aliases.values())).pop()
        for raw in rows:
            if raw.get("team_name") not in aliases:
                aliases[str(raw["team_name"])] = remaining
    for raw in rows:
        side = next(
            (
                side
                for side in ("home", "away")
                if known(raw.get("team_name"))
                and team_for(game, side).name == raw["team_name"]
            ),
            aliases.get(str(raw.get("team_name"))),
        )
        if side is None:
            raise ConversionError(
                f"Cannot resolve box score team: {raw.get('team_name')!r}"
            )
        team = team_for(game, side)
        if raw.get("totals"):
            totals = fields(raw["totals"])
            if source == "baskethotel":
                totals = {
                    k: v
                    for k, v in totals.items()
                    if k not in {"rebounds", "assists", "steals"}
                }
            team.totals.CopyFrom(stats(totals))
        for row in records(raw.get("players")):
            if source == "baskethotel" and row.get("MIN") == "-":
                row = {**row, "MIN": None}
            name = first(row, "Player", "Pelaaja", "name")
            jersey = first(row, "Shirt Number", "shirtNumber", "number")
            if source == "baskethotel" and known(name):
                match = re.match(r"^#\s*(\d+)\s*(.+)$", str(name))
                if match:
                    jersey, name = match.groups()
            p = player(
                team,
                name=text(name),
                jersey=jersey,
                native_id=row.get("native_id"),
                namespace=f"{source}.player",
                scope=f"{game.id}:{side}",
            )
            p.stats.CopyFrom(stats(row, participation=True))
    return aliases


def append_events(
    game: pb.Game,
    rows: list[Mapping[str, object]],
    source: str,
    regulation_periods: int | None,
    *,
    clock_mode: str | None = "remaining",
    team_aliases: dict[str, str] | None = None,
) -> None:
    for index, raw in enumerate(rows):
        kind = str(raw.get("event_type") or "").lower()
        subtype = str(first(raw, "detail", "action") or "").lower()
        if kind in {"game", "period"} and subtype in {"start", "end"}:
            kind += subtype
        event_type = EVENT_TYPES.get(
            kind,
            {
                "shot_made": pb.EVENT_TYPE_SHOT,
                "shot_missed": pb.EVENT_TYPE_SHOT,
                "def_rebound": pb.EVENT_TYPE_REBOUND,
                "off_rebound": pb.EVENT_TYPE_REBOUND,
                "game_start": pb.EVENT_TYPE_GAME_START,
                "game_end": pb.EVENT_TYPE_GAME_END,
                "period_start": pb.EVENT_TYPE_PERIOD_START,
                "period_end": pb.EVENT_TYPE_PERIOD_END,
                "sub_in": pb.EVENT_TYPE_SUBSTITUTION,
                "sub_out": pb.EVENT_TYPE_SUBSTITUTION,
            }.get(kind, pb.EVENT_TYPE_OTHER),
        )
        action = first(raw, "action_raw", "action")
        lowered = str(action or "").lower()
        event = game.events.add(
            id=str(raw.get("event_id") or f"{source}:row:{index}"), type=event_type
        )
        put(event, "period", _period(raw.get("period"), regulation_periods))
        if clock_mode == "remaining":
            put(event, "game_clock", clock(raw.get("time")))
        elif clock_mode is not None:
            raise ConversionError("Only verified remaining-period clocks are supported")
        put(event, "description", action)
        put(event, "detail", raw.get("detail") or action)
        if known(raw.get("score")):
            match = re.fullmatch(r"(\d+)\s*[-:]\s*(\d+)", str(raw["score"]))
            if not match:
                raise ConversionError(f"Invalid event score: {raw['score']!r}")
            put(event, "home_score", count(match[1]))
            put(event, "away_score", count(match[2]))
        side = {"1": "home", "2": "away"}.get(str(raw.get("team")))
        if side is None:
            side = (team_aliases or {}).get(str(raw.get("team")))
        if side is None:
            side = next(
                (
                    s
                    for s in ("home", "away")
                    if known(raw.get("team")) and team_for(game, s).name == raw["team"]
                ),
                None,
            )
        if side:
            event.home = side == "home"
        p = None
        if side:
            actors = records(raw.get("players")) or [
                {"name": raw.get("player_name"), "number": raw.get("player_number")}
            ]
            for actor_index, actor in enumerate(actors):
                if not known(actor.get("name")):
                    continue
                actor_side = side
                if source == "baskethotel":
                    roster_sides = [
                        s
                        for s in ("home", "away")
                        for candidate in team_for(game, s).players
                        if candidate.name == actor["name"]
                        and (
                            not known(actor.get("number"))
                            or candidate.jersey_number == str(actor["number"])
                        )
                    ]
                    if len(roster_sides) == 1:
                        actor_side = roster_sides[0]
                    elif actor_index > 0 and "rikottu pelaaja" in lowered:
                        actor_side = "away" if side == "home" else "home"
                actor_player = player(
                    team_for(game, actor_side),
                    native_id=actor.get("player_id")
                    if source == "baskethotel"
                    else None,
                    name=text(actor.get("name")),
                    jersey=actor.get("number"),
                    namespace=f"{source}.player",
                    scope=f"{game.id}:{actor_side}",
                )
                if actor_index == 0:
                    p = actor_player
                participant = event.players.add(player_id=actor_player.id)
                put(
                    participant,
                    "role",
                    "fouled"
                    if actor_index > 0 and "rikottu pelaaja" in lowered
                    else {"sub_in": "incoming", "sub_out": "outgoing"}.get(
                        kind, ROLES.get(event_type)
                    ),
                )
                native_id = actor.get("player_id")
                if (
                    source == "baskethotel"
                    and known(native_id)
                    and not any(
                        e.namespace == "baskethotel.player"
                        and e.value == str(native_id)
                        for e in actor_player.external_ids
                    )
                ):
                    external(
                        actor_player,
                        "baskethotel.player",
                        native_id,
                        f"league:{actor.get('league_id', '')}",
                    )
                if source == "baskethotel" and known(native_id):
                    for identity in actor_player.external_ids:
                        if identity.namespace == "baskethotel.player" and known(
                            actor.get("league_id")
                        ):
                            identity.scope = f"league:{actor['league_id']}"
        if event_type == pb.EVENT_TYPE_SHOT:
            value = {"2pt": 2, "3pt": 3, "freethrow": 1}.get(kind)
            if value is None:
                match = re.search(r"([123])\s*(?:pt|pisteen)", lowered)
                value = int(match[1]) if match else None
            put(event, "point_value", value)
            outcome = raw.get("success")
            if not known(outcome):
                if (
                    kind == "shot_missed"
                    or "missed" in lowered
                    or "epäonnistui" in lowered
                ):
                    outcome = False
                elif kind == "shot_made" or "made" in lowered or "onnistui" in lowered:
                    outcome = True
            put(event, "made", boolean(outcome))
            if p is not None and len(event.players) == 1:
                shot = p.shots.add(event_id=event.id)
                for field in ("period", "game_clock", "point_value", "made"):
                    if event.HasField(field):
                        setattr(
                            shot,
                            field,
                            {
                                "period": event.period,
                                "game_clock": event.game_clock,
                                "point_value": event.point_value,
                                "made": event.made,
                            }[field],
                        )
                put(shot, "shot_type", action)
                put(shot, "x", number(raw.get("x")))
                put(shot, "y", number(raw.get("y")))


def from_genius(
    *,
    match_id: str,
    boxscore: Mapping[str, object] | str | None = None,
    playbyplay: Mapping[str, object] | str | None = None,
    basketfi: Mapping[str, object] | None = None,
    regulation_periods: int | None = None,
) -> pb.Game:
    """Accept source-parsed dicts or raw HTML. Do not pass normalized exports."""
    box = (
        GeniusSportsParser.parse_boxscore_html(boxscore)
        if isinstance(boxscore, str)
        else boxscore
    )
    pbp = (
        GeniusSportsParser.parse_playbyplay_html(playbyplay, include_analysis=False)
        if isinstance(playbyplay, str)
        else playbyplay
    )
    box = fields(box) if box is not None else None
    pbp = fields(pbp) if pbp is not None else None
    info = fields((box or pbp or {}).get("match_info"))
    game = base_game("genius", match_id, info, basketfi)
    if info.get("datetime") and basketfi is None:
        date = datetime.strptime(str(info["datetime"]), "%b %d, %Y, %I:%M %p")
        game.date, game.time = date.strftime("%Y-%m-%d"), date.strftime("%H:%M")
    if box is not None:
        _ = _box(game, box, "genius")
    if pbp is not None:
        append_events(game, records(pbp.get("events")), "genius", regulation_periods)
    return finish(game)


def from_baskethotel(
    *,
    game_id: str,
    league_id: str,
    season: str,
    header: Mapping[str, object] | str | None = None,
    boxscore: Mapping[str, object] | str | None = None,
    playbyplay: Mapping[str, object] | str | None = None,
    status: str | None = None,
    regulation_periods: int | None = None,
    clock_mode: str | None = None,
) -> pb.Game:
    """Convert raw widget HTML/JavaScript or parsed responses. Clock semantics must be explicit."""

    def html(value: str) -> str:
        return BasketHotelParser.extract_html_from_response(value)

    meta = (
        BasketHotelParser.parse_game_html(html(header))
        if isinstance(header, str)
        else (header or {})
    )
    meta = fields(meta)
    game_info = fields(meta.get("game_info"))
    reported_id = game_info.get("game_id")
    if known(reported_id) and str(reported_id) != str(game_id):
        raise ConversionError("BasketHotel header belongs to a different game")
    box = (
        BasketHotelParser.parse_boxscore_html(html(boxscore))
        if isinstance(boxscore, str)
        else boxscore
    )
    pbp = (
        BasketHotelParser.parse_playbyplay_html(html(playbyplay))
        if isinstance(playbyplay, str)
        else playbyplay
    )
    box = fields(box) if box is not None else None
    pbp = fields(pbp) if pbp is not None else None
    info: dict[str, object] = {"status": status}
    for side in ("home", "away"):
        info[f"{side}_team"] = fields(fields(meta.get("teams")).get(side)).get(
            "name"
        ) or fields((pbp or {}).get("teams")).get(side)
        info[f"{side}_score"] = fields(meta.get("score")).get(side)
    game = base_game(
        "baskethotel", game_id, info, scope=f"league:{league_id}:season:{season}"
    )
    game.league, game.season = str(league_id), str(season)
    date = game_info.get("date")
    if date:
        game.date = datetime.strptime(str(date), "%d.%m.%Y").strftime("%Y-%m-%d")
    put(game, "time", game_info.get("time"))
    aliases = _box(game, box, "baskethotel") if box is not None else {}
    if pbp is not None:
        append_events(
            game,
            records(pbp.get("events")),
            "baskethotel",
            regulation_periods,
            clock_mode=clock_mode,
            team_aliases=aliases,
        )
    return finish(game)
