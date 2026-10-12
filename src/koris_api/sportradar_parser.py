"""Pure parsers for the Basket.fi Sportradar widget feed.

Keep the existing export shapes so consumers can use old and new seasons alike.
Source identities and official totals are retained alongside the parsed values.
"""

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import TypedDict

from .analysis.possessions import calculate_possessions
from .data import fields, records, text


class PlayByPlay(TypedDict):
    source: str
    match_info: dict[str, object]
    events: list[dict[str, object]]
    players: list[dict[str, object]]
    possessions: Mapping[str, object]


_STAT_FIELDS = {
    "points": "Points",
    "pointsTwoMade": "2 Points Made",
    "pointsTwoAttempted": "2 Points Attempted",
    "pointsTwoPercentage": "2 Points Percentage",
    "pointsThreeMade": "3 Points Made",
    "pointsThreeAttempted": "3 Points Attempted",
    "pointsThreePercentage": "3 Point Percentage",
    "freeThrowsMade": "Free Throws Made",
    "freeThrowsAttempted": "Free Throws Attempted",
    "freeThrowsPercentage": "Free Throw Percentage",
    "reboundsOffensive": "Offensive Rebounds",
    "reboundsDefensive": "Defensive Rebounds",
    "rebounds": "Total Rebounds",
    "assists": "Assists",
    "steals": "Steals",
    "turnovers": "Turnovers",
    "blocks": "Blocks",
    "foulsTotal": "Personal Foul",
    "plusMinus": "Plus/Minus",
    "efficiency": "Index of Success",
}


def duration_clock(value: str | None) -> str | None:
    """Convert ISO time durations to MM:SS, retaining fractional seconds."""
    if value is None:
        return None
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not match or not any(match.groups()):
        raise ValueError(f"Invalid Sportradar duration: {value!r}")
    hours, minutes, seconds = match.groups()
    total = (
        Decimal(hours or 0) * 3600 + Decimal(minutes or 0) * 60 + Decimal(seconds or 0)
    )
    whole_minutes = int(total // 60)
    remaining = total % 60
    whole_seconds, separator, fraction = format(remaining, "f").partition(".")
    second_text = whole_seconds.zfill(2) + separator + fraction
    return f"{whole_minutes:02d}:{second_text}"


def _competitors(
    data: Mapping[str, object],
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    competitors = records(fields(data.get("fixture")).get("competitors"))
    home = next((c for c in competitors if c.get("isHome") is True), None)
    away = next((c for c in competitors if c.get("isHome") is False), None)
    if home is None or away is None:
        raise ValueError("Sportradar fixture missing home/away competitors")
    return home, away


def _match_info(
    home: Mapping[str, object], away: Mapping[str, object]
) -> dict[str, object]:
    return {
        "home_team": home.get("name"),
        "away_team": away.get("name"),
        "home_score": home.get("score"),
        "away_score": away.get("score"),
    }


def _stats(stats: Mapping[str, object]) -> dict[str, object]:
    return {
        target: stats[source]
        for source, target in _STAT_FIELDS.items()
        if source in stats
    }


def parse_boxscore(data: Mapping[str, object]) -> dict[str, object]:
    home, away = _competitors(data)
    base = fields(fields(data.get("statistics")).get("data")).get("base")
    if base is None:
        raise ValueError("Sportradar fixture missing box score")
    base = fields(base)
    teams: list[dict[str, object]] = []
    for side, competitor in (("home", home), ("away", away)):
        team = fields(base.get(side))
        if team.get("entity") is None:
            raise ValueError(f"Sportradar box score missing {side} totals")
        entity = fields(team["entity"])
        players = [
            {
                **_stats(fields(row.get("statistics"))),
                "Player": row.get("personName"),
                "Shirt Number": row.get("bib"),
                "Minutes": duration_clock(
                    text(fields(row.get("statistics")).get("minutes"))
                ),
                "player_id": row.get("personId"),
                "starter": row.get("starter"),
                "played": row.get("participated"),
            }
            for group in records(team.get("persons"))
            for row in records(group.get("rows"))
        ]
        if not players:
            raise ValueError(f"Sportradar box score missing {side} players")
        teams.append(
            {
                "team_name": competitor.get("name"),
                "players": players,
                "totals": _stats(entity),
            }
        )
    return {
        "source": "sportradar",
        "match_info": _match_info(home, away),
        "teams": teams,
    }


def parse_playbyplay(data: Mapping[str, object]) -> PlayByPlay:
    home, away = _competitors(data)
    team_ids = {str(home["entityId"]): "1", str(away["entityId"]): "2"}
    players: dict[str, dict[str, object]] = {}
    events: list[dict[str, object]] = []
    overtime = 0
    for period in sorted(
        records(data.get("periodData")), key=lambda p: int(str(p["periodId"]))
    ):
        if period.get("isOvertime"):
            overtime += 1
        label = (
            f"OT{overtime}" if period.get("isOvertime") else f"Q{period['periodId']}"
        )
        # The public feed is chronological, including the order of equal-clock events.
        for raw in records(period.get("events")):
            team = team_ids.get(str(raw.get("entityId")), "0")
            person_id = raw.get("personId")
            if person_id:
                players[str(person_id)] = {
                    "player_id": person_id,
                    "name": raw.get("name"),
                    "number": raw.get("bib"),
                    "team": team,
                }
            kind = str(raw.get("eventType") or "").lower()
            action = raw.get("desc") or raw.get("eventSubType") or kind
            if kind in {"2pt", "3pt", "freethrow"}:
                outcome = (
                    "made"
                    if raw.get("success") is True
                    else "missed"
                    if raw.get("success") is False
                    else None
                )
                if outcome:
                    action = f"{outcome} {action}"
            scores = fields(raw.get("scores"))
            home_score, away_score = (
                scores.get(str(home["entityId"])),
                scores.get(str(away["entityId"])),
            )
            events.append(
                {
                    "event_id": raw.get("eventId"),
                    "event_type": kind,
                    "period": label,
                    "team": team,
                    "time": duration_clock(text(raw.get("clock"))),
                    "score": f"{home_score}-{away_score}"
                    if home_score is not None and away_score is not None
                    else None,
                    "action": action,
                    "player_number": raw.get("bib"),
                    "player_name": raw.get("name"),
                    "player_id": person_id,
                    "success": raw.get("success"),
                    "x": raw.get("x"),
                    "y": raw.get("y"),
                }
            )
    if not events:
        raise ValueError("Sportradar fixture missing play-by-play events")
    return {
        "source": "sportradar",
        "match_info": _match_info(home, away),
        "events": events,
        "players": list(players.values()),
        "possessions": fields(calculate_possessions(events)),
    }
