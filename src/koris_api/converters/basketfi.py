"""Convert untouched getMatch/getMatches game rows to koris.v1 metadata."""

from collections.abc import Mapping

from ..data import fields
from .common import (
    STATUS,
    ConversionError,
    count,
    external,
    finish,
    first,
    known,
    pb,
    put,
    team_for,
)


def from_basketfi(response: Mapping[str, object]) -> pb.Game:
    row = fields(response.get("match", response))
    if not known(row.get("match_id")):
        raise ConversionError("Basket.fi match_id is required")
    game = pb.Game(id=f"basketfi:match:{row['match_id']}")
    external(game, "basketfi.match", row["match_id"])
    external_id = row.get("match_external_id")
    if known(external_id):
        from ..sportradar_api import is_sportradar_id

        external(
            game,
            "sportradar.fixture"
            if is_sportradar_id(str(external_id))
            else "genius.match",
            external_id,
        )
    for side, suffix in (("home", "A"), ("away", "B")):
        team = team_for(game, side)
        put(team, "name", row.get(f"team_{suffix}_name"))
        external(team, "basketfi.team", row.get(f"team_{suffix}_id"))
        put(team, "score", count(row.get(f"fs_{suffix}")))
    put(game, "date", row.get("date"))
    put(game, "time", str(row["time"])[:5] if known(row.get("time")) else None)
    put(game, "league", row.get("category_name"))
    put(game, "season", first(row, "competition_season", "season_id", "competition_id"))
    game.status = STATUS.get(
        str(row.get("status", "")).lower(), pb.GAME_STATUS_UNSPECIFIED
    )
    return finish(game)
