"""Pure conversion helpers. Missing data stays missing; malformed data fails."""

import math
import re
from collections.abc import Mapping
from decimal import Decimal

from google.protobuf.message import Message

from ..schema.koris.v1 import game_pb2 as pb


class ConversionError(ValueError):
    """A source value cannot be represented faithfully in koris.v1."""


def known(value: object) -> bool:
    return value is not None and value != ""


def first(row: Mapping[str, object], *keys: str) -> object:
    return next((row[k] for k in keys if k in row and known(row[k])), None)


def put(message: Message, field: str, value: object) -> None:
    if known(value):
        setattr(message, field, value)


def count(value: object) -> int | None:
    if not known(value):
        return None
    try:
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or number != int(number)
            or not 0 <= number <= 4294967295
        ):
            raise ValueError()
        return int(number)
    except (ValueError, ArithmeticError):
        raise ConversionError(f"Invalid unsigned count: {value!r}") from None


def number(value: object) -> float | None:
    if not known(value):
        return None
    try:
        if not isinstance(value, (str, int, float, Decimal)):
            raise TypeError()
        result = float(value)
        if not math.isfinite(result):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ConversionError(f"Invalid finite number: {value!r}") from None


def boolean(value: object) -> bool | None:
    if not known(value):
        return None
    if value in (True, 1, "1", "true", "True"):
        return True
    if value in (False, 0, "0", "false", "False"):
        return False
    raise ConversionError(f"Invalid boolean: {value!r}")


def seconds(value: object, *, minutes: bool = False) -> float | None:
    if not known(value):
        return None
    text = str(value)
    if text.startswith("PT"):
        match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", text)
        if not match or not any(match.groups()):
            raise ConversionError(f"Invalid duration: {value!r}")
        h, m, s = match.groups()
        result = float(Decimal(h or 0) * 3600 + Decimal(m or 0) * 60 + Decimal(s or 0))
    elif ":" in text:
        # Legacy Genius uses MM:SS:hundredths.
        match = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)(?::(\d{1,2}))?", text)
        if not match or Decimal(match[2]) >= 60:
            raise ConversionError(f"Invalid clock: {value!r}")
        result = float(
            Decimal(match[1]) * 60 + Decimal(match[2]) + Decimal(match[3] or 0) / 100
        )
    else:
        numeric = number(value)
        if numeric is None:
            return None
        result = numeric * (60 if minutes else 1)
    if not math.isfinite(result) or result < 0:
        raise ConversionError(f"Invalid duration: {value!r}")
    return result


def clock(value: object) -> str | None:
    duration = seconds(value)
    if duration is None:
        return None
    value = Decimal(str(duration))
    whole, fraction = divmod(value, 60)
    sec = (
        format(fraction, "f").rstrip("0").rstrip(".")
        if fraction % 1
        else str(int(fraction))
    )
    integer, dot, decimal = sec.partition(".")
    return f"{int(whole):02d}:{integer.zfill(2)}{dot}{decimal}"


def external(
    message: pb.Game | pb.Team | pb.Player | pb.Shot,
    namespace: str,
    value: object,
    scope: object = None,
) -> None:
    if known(value):
        item = message.external_ids.add(namespace=namespace, value=str(value))
        put(item, "scope", scope)


STATUS = {
    "played": pb.GAME_STATUS_FINAL,
    "final": pb.GAME_STATUS_FINAL,
    "confirmed": pb.GAME_STATUS_FINAL,
    "finished": pb.GAME_STATUS_FINAL,
    "fixture": pb.GAME_STATUS_SCHEDULED,
    "planned": pb.GAME_STATUS_SCHEDULED,
    "scheduled": pb.GAME_STATUS_SCHEDULED,
    "live": pb.GAME_STATUS_IN_PROGRESS,
    "in_progress": pb.GAME_STATUS_IN_PROGRESS,
    "postponed": pb.GAME_STATUS_POSTPONED,
    "cancelled": pb.GAME_STATUS_CANCELLED,
    "canceled": pb.GAME_STATUS_CANCELLED,
}


def finish(game: pb.Game) -> pb.Game:
    if (
        game.status == pb.GAME_STATUS_FINAL
        and game.home.HasField("score")
        and game.away.HasField("score")
        and game.home.score != game.away.score
    ):
        game.home.win = game.home.score > game.away.score
        game.away.win = not game.home.win
    return game


def player(
    team: pb.Team,
    *,
    native_id: object = None,
    name: str | None = None,
    jersey: object = None,
    namespace: str,
    scope: str,
) -> pb.Player:
    """Resolve by native ID first; name+jersey only for source rows without IDs."""
    candidates = [
        p
        for p in team.players
        if (
            any(
                e.namespace == namespace and e.value == str(native_id)
                for e in p.external_ids
            )
            if known(native_id)
            else known(name)
            and p.name == name
            and p.jersey_number == (str(jersey) if known(jersey) else "")
        )
    ]
    if len(candidates) > 1:
        raise ConversionError("Ambiguous player identity")
    if not candidates and known(native_id) and known(name):
        candidates = [
            p
            for p in team.players
            if p.name == name
            and p.jersey_number == (str(jersey) if known(jersey) else "")
            and not any(e.namespace == namespace for e in p.external_ids)
        ]
        if len(candidates) > 1:
            raise ConversionError("Ambiguous player identity")
    if candidates:
        if known(native_id) and not any(
            e.namespace == namespace for e in candidates[0].external_ids
        ):
            external(candidates[0], namespace, native_id)
        return candidates[0]
    p = team.players.add(
        id=f"{'home' if scope.endswith(':home') else 'away'}:player:{len(team.players) + 1}"
    )
    put(p, "name", name)
    put(p, "jersey_number", str(jersey) if known(jersey) else None)
    external(p, namespace, native_id)
    return p


FIELDS = {
    "points": ("Points", "PIST", "sPoints", "points"),
    "two_pointers_made": ("2 Points Made", "sTwoPointersMade", "pointsTwoMade"),
    "two_pointers_attempted": (
        "2 Points Attempted",
        "sTwoPointersAttempted",
        "pointsTwoAttempted",
    ),
    "three_pointers_made": ("3 Points Made", "sThreePointersMade", "pointsThreeMade"),
    "three_pointers_attempted": (
        "3 Points Attempted",
        "3 Points Atttempted",
        "sThreePointersAttempted",
        "pointsThreeAttempted",
    ),
    "free_throws_made": ("Free Throws Made", "sFreeThrowsMade", "freeThrowsMade"),
    "free_throws_attempted": (
        "Free Throws Attempted",
        "sFreeThrowsAttempted",
        "freeThrowsAttempted",
    ),
    "offensive_rebounds": (
        "Offensive Rebounds",
        "HL",
        "sReboundsOffensive",
        "reboundsOffensive",
    ),
    "defensive_rebounds": (
        "Defensive Rebounds",
        "PL",
        "sReboundsDefensive",
        "reboundsDefensive",
    ),
    "rebounds": ("Total Rebounds", "LEV", "sReboundsTotal", "rebounds"),
    "assists": ("Assists", "S", "sAssists", "assists"),
    "steals": ("Steals", "R", "sSteals", "steals"),
    "turnovers": ("Turnovers", "M", "sTurnovers", "turnovers"),
    "blocks": ("Blocks", "T+", "sBlocks", "blocks"),
    "personal_fouls": (
        "Personal Foul",
        "Personal Fouls",
        "V-",
        "sFoulsPersonal",
        "foulsTotal",
    ),
}


def stats(row: Mapping[str, object], *, participation: bool = False) -> pb.Stats:
    result = pb.Stats()
    for field, keys in FIELDS.items():
        put(result, field, count(first(row, *keys)))
    for key, prefix in (
        ("2P", "two_pointers"),
        ("3P", "three_pointers"),
        ("1P", "free_throws"),
    ):
        if known(row.get(key)):
            match = re.fullmatch(r"\s*(\d+)\s*[-/]\s*(\d+)\s*", str(row[key]))
            if not match:
                raise ConversionError(f"Invalid made-attempted pair: {row[key]!r}")
            setattr(result, prefix + "_made", count(match[1]))
            setattr(result, prefix + "_attempted", count(match[2]))
    if participation:
        duration = first(row, "Minutes", "MIN", "sMinutes", "minutes")
        dnp = str(duration).strip().lower() in {"dnp", "did not play", "ei pelannut"}
        if dnp:
            result.played = False
        else:
            put(result, "seconds_played", seconds(duration, minutes=True))
        put(result, "starter", boolean(first(row, "starter")))
        put(result, "played", boolean(first(row, "played", "participated")))
    return result


def team_for(game: pb.Game, side: str) -> pb.Team:
    """Keep home/away access typed and reject unresolved sides."""
    if side == "home":
        return game.home
    if side == "away":
        return game.away
    raise ConversionError(f"Unknown team side: {side!r}")
