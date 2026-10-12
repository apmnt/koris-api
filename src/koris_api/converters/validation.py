"""Report semantic issues without changing source-reported values."""

from typing import cast

from google.protobuf.descriptor import FieldDescriptor

from .common import pb, team_for


def validate_game(game: pb.Game) -> list[str]:
    issues: list[str] = []
    players = [p for team in (game.home, game.away) for p in team.players]
    ids = [p.id for p in players if p.HasField("id")]
    if len(ids) != len(set(ids)):
        issues.append("Duplicate player IDs")
    events = {e.id: e for e in game.events}
    if len(events) != len(game.events):
        issues.append("Duplicate event IDs")
    for e in game.events:
        for actor in e.players:
            if actor.player_id not in ids:
                issues.append(f"Event {e.id}: unknown player {actor.player_id}")
    for side in ("home", "away"):
        team = team_for(game, side)
        if (
            team.HasField("score")
            and team.totals.HasField("points")
            and team.score != team.totals.points
        ):
            issues.append(f"{side}: score and reported total points disagree")
        if (
            game.status == pb.GAME_STATUS_FINAL
            and team.totals.HasField("points")
            and team.players
            and all(p.stats.HasField("points") for p in team.players)
            and sum(p.stats.points for p in team.players) != team.totals.points
        ):
            issues.append(f"{side}: player points do not sum to reported total")
        rows = [(f"{side}.totals", team.totals)] + [
            (p.id, p.stats) for p in team.players
        ]
        for label, stats in rows:
            for prefix, made_value, attempted_value in (
                ("two_pointers", stats.two_pointers_made, stats.two_pointers_attempted),
                (
                    "three_pointers",
                    stats.three_pointers_made,
                    stats.three_pointers_attempted,
                ),
                ("free_throws", stats.free_throws_made, stats.free_throws_attempted),
            ):
                made, attempts = prefix + "_made", prefix + "_attempted"
                if (
                    stats.HasField(made)
                    and stats.HasField(attempts)
                    and made_value > attempted_value
                ):
                    issues.append(f"{label}: {made} exceeds attempts")
            if (
                stats.HasField("played")
                and not stats.played
                and any(
                    field.name not in {"played", "starter"} and value > 0
                    for field, value in cast(
                        list[tuple[FieldDescriptor, int | float | bool]],
                        stats.ListFields(),
                    )
                )
            ):
                issues.append(f"{label}: DNP conflicts with positive stats")
        for p in team.players:
            for shot in p.shots:
                if shot.HasField("event_id"):
                    event = events.get(shot.event_id)
                    if (
                        event is None
                        or event.type != pb.EVENT_TYPE_SHOT
                        or p.id not in {a.player_id for a in event.players}
                    ):
                        issues.append(
                            f"{p.id}: invalid shot event link {shot.event_id}"
                        )
    return issues
