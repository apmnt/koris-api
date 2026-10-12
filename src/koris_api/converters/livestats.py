"""Convert untouched Genius FIBA LiveStats data.json payloads."""

import re
from collections.abc import Mapping

from ..data import fields, records, text
from .common import (
    ConversionError,
    boolean,
    clock,
    count,
    external,
    first,
    known,
    number,
    pb,
    player,
    put,
    stats,
    team_for,
)
from .legacy import append_events, base_game


def from_genius_livestats(
    data: Mapping[str, object],
    *,
    match_id: str,
    basketfi: Mapping[str, object] | None = None,
    regulation_periods: int | None = None,
) -> pb.Game:
    teams = fields(data.get("tm"))
    if not all(slot in teams for slot in ("1", "2")):
        raise ConversionError("LiveStats missing home/away teams")
    info = {
        f"{side}_{key}": fields(teams[slot]).get(source)
        for side, slot in (("home", "1"), ("away", "2"))
        for key, source in (("team", "name"), ("score", "score"))
    }
    # Feed scores alone do not establish final status.
    game = base_game("genius", match_id, info, basketfi)
    for side, slot in (("home", "1"), ("away", "2")):
        team = team_for(game, side)
        raw = fields(teams[slot])
        totals = {
            **raw,
            **{
                key.removeprefix("tot_"): value
                for key, value in raw.items()
                if key.startswith("tot_")
            },
        }
        team.totals.CopyFrom(stats(totals))
        for roster_slot, value in fields(raw.get("pl")).items():
            row = fields(value)
            name = (
                text(first(row, "name"))
                or " ".join(
                    str(row.get(k) or "").strip() for k in ("firstName", "familyName")
                ).strip()
                or None
            )
            p = player(
                team,
                name=name,
                jersey=row.get("shirtNumber"),
                namespace="genius.player",
                scope=f"{game.id}:{side}",
            )
            external(p, "genius.roster_slot", roster_slot, f"{game.id}:{side}")
            minutes = str(row.get("sMinutes") or "")
            overflow = re.fullmatch(r"(\d+):60", minutes)
            # This feed rounds accumulated playing time before formatting it.
            # Carry exactly 60 seconds; retain strict clocks for every other input.
            if overflow:
                row = {**row, "sMinutes": f"{int(overflow[1]) + 1}:00"}
            p.stats.CopyFrom(stats(row, participation=True))
    rows: list[Mapping[str, object]] = []
    raw_events = records(data.get("pbp", []))
    # data.json lists newest first; preserve equal-clock ordering when reversing.
    for index, raw in enumerate(reversed(raw_events)):
        period = raw.get("period")
        # The established LiveStats convention numbers OT periods separately.
        if str(raw.get("periodType", "")).upper() == "OVERTIME":
            if regulation_periods is None:
                raise ConversionError("LiveStats overtime requires regulation_periods")
            period = regulation_periods + (count(period) or 0)
        name = (
            text(first(raw, "player", "name"))
            or " ".join(
                str(raw.get(k) or "").strip() for k in ("firstName", "familyName")
            ).strip()
            or None
        )
        side = {"1": "home", "2": "away"}.get(str(raw.get("tno")))
        jersey = raw.get("shirtNumber")
        if side is not None and known(raw.get("pno")):
            roster = [
                p
                for p in team_for(game, side).players
                if any(
                    e.namespace == "genius.roster_slot" and e.value == str(raw["pno"])
                    for e in p.external_ids
                )
            ]
            if len(roster) == 1:
                if known(jersey) and str(jersey) != roster[0].jersey_number:
                    raise ConversionError(
                        "LiveStats event jersey disagrees with roster slot"
                    )
                name, jersey = roster[0].name, roster[0].jersey_number
        event_row: dict[str, object] = {
            "event_id": str(
                raw.get("actionNumber")
                if known(raw.get("actionNumber"))
                else f"genius:row:{index}"
            ),
            "event_type": raw.get("actionType"),
            "period": period,
            "team": str(raw.get("tno", "0")),
            "time": raw.get("gt"),
            "action": raw.get("subType"),
            "detail": raw.get("subType"),
            "player_name": name,
            "player_number": jersey,
            "success": raw.get("success"),
            "x": raw.get("x"),
            "y": raw.get("y"),
        }
        if known(raw.get("s1")) and known(raw.get("s2")):
            event_row["score"] = f"{raw['s1']}-{raw['s2']}"
        rows.append(event_row)
    append_events(game, rows, "genius", regulation_periods)
    for team in (game.home, game.away):
        for p in team.players:
            for shot in p.shots:
                if not shot.event_id.startswith("genius:row:"):
                    external(shot, "genius.action_number", shot.event_id, game.id)
    # Shot charts can contain geometry absent from pbp. Join only by an action
    # number and a verified team-local roster slot; never join by clock alone.
    for side, slot in (("home", "1"), ("away", "2")):
        team = team_for(game, side)
        for raw in records(fields(teams[slot]).get("shot")):
            roster_slot = first(raw, "pno", "p")
            candidates = [
                p
                for p in team.players
                if any(
                    e.namespace == "genius.roster_slot" and e.value == str(roster_slot)
                    for e in p.external_ids
                )
            ]
            if len(candidates) != 1:
                raise ConversionError("Cannot resolve LiveStats shot roster slot")
            p = candidates[0]
            action = raw.get("actionNumber")
            linked = [
                shot
                for shot in p.shots
                if known(action)
                and (
                    shot.event_id == str(action)
                    or any(
                        e.namespace == "genius.action_number" and e.value == str(action)
                        for e in shot.external_ids
                    )
                )
            ]
            if len(linked) > 1:
                raise ConversionError("Duplicate LiveStats shot action")
            event = next(
                (
                    event
                    for event in game.events
                    if known(action) and event.id == str(action)
                ),
                None,
            )
            if event is not None and (
                event.type != pb.EVENT_TYPE_SHOT
                or p.id not in {actor.player_id for actor in event.players}
            ):
                raise ConversionError(
                    "LiveStats shot action disagrees with event player/type"
                )
            shot = linked[0] if linked else p.shots.add()
            if event is not None:
                shot.event_id = event.id
            for field in ("x", "y"):
                value = number(raw.get(field))
                if (
                    value is not None
                    and shot.HasField(field)
                    and (shot.x if field == "x" else shot.y) != value
                ):
                    raise ConversionError(
                        "LiveStats shot geometry disagrees with play-by-play"
                    )
                put(shot, field, value)
            made = boolean(raw.get("r"))
            if made is not None and shot.HasField("made") and shot.made != made:
                raise ConversionError(
                    "LiveStats shot result disagrees with play-by-play"
                )
            put(shot, "made", made)
            put(
                shot,
                "point_value",
                {"2pt": 2, "3pt": 3, "freethrow": 1}.get(str(raw.get("actionType"))),
            )
            period = count(raw.get("per"))
            if str(raw.get("perType", "")).upper() == "OVERTIME" and period is not None:
                if regulation_periods is None:
                    raise ConversionError(
                        "LiveStats overtime requires regulation_periods"
                    )
                period += regulation_periods
            put(shot, "period", period)
            put(shot, "game_clock", clock(raw.get("gt")))
            put(shot, "shot_type", raw.get("subType"))
            if known(action) and not any(
                e.namespace == "genius.action_number" and e.value == str(action)
                for e in shot.external_ids
            ):
                external(shot, "genius.action_number", action, game.id)
    if game.events and game.events[-1].type == pb.EVENT_TYPE_GAME_END:
        game.status = pb.GAME_STATUS_FINAL
    from .common import finish

    return finish(game)
