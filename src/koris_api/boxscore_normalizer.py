"""Normalize boxscore data across Genius and BasketHotel sources."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import TypedDict

from .data import fields, records, text


class NormalizedTeam(TypedDict):
    team_name: str | None
    players: list[dict[str, object]]
    totals: Mapping[str, object]


class NormalizedBoxScore(TypedDict):
    source: str | None
    teams: list[NormalizedTeam]
    match_totals: dict[str, int | float]


def _to_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(value) if isinstance(value, (str, int, float, Decimal)) else 0
    except (TypeError, ValueError):
        return 0


def _to_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        cleaned = value.replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    try:
        return float(value) if isinstance(value, (str, int, float, Decimal)) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_minutes_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if ":" in value:
            return value
        if value.strip().isdigit():
            return f"{int(value.strip())}:00"
        return value
    try:
        if not isinstance(value, (int, float, Decimal)):
            return None
        minutes = int(float(value))
        return f"{minutes}:00"
    except (TypeError, ValueError):
        return None


def _parse_made_attempt(value: object) -> tuple[int, int]:
    if value is None:
        return (0, 0)
    if isinstance(value, str) and "-" in value:
        parts = value.split("-", 1)
        if len(parts) == 2:
            return (_to_int(parts[0]), _to_int(parts[1]))
    return (_to_int(value), 0)


def _percentage_from(value: object, made: int, attempted: int) -> float:
    if value is not None and value != "":
        return _to_float(value)
    if attempted <= 0:
        return 0.0
    return round((made / attempted) * 100.0, 1)


def _detect_source(players: list[Mapping[str, object]]) -> str:
    if not players:
        return "unknown"
    sample = players[0]
    keys = {str(k) for k in sample}
    if "PIST" in keys or "LEV" in keys:
        return "baskethotel"
    if "Points" in keys or "Total Rebounds" in keys:
        return "genius"
    return "unknown"


def _extract_player_number(name: str | None) -> tuple[int | None, str | None]:
    if not name:
        return (None, name)
    match = re.match(r"^#\s*(\d+)\s*(.*)$", str(name).strip())
    if not match:
        return (None, name)
    number = int(match.group(1))
    cleaned = match.group(2).strip() or None
    return (number, cleaned)


def _normalize_player_genius(
    player: Mapping[str, object], team_name: str | None
) -> dict[str, object]:
    player_number = player.get("Shirt Number")
    two_made = _to_int(player.get("2 Points Made"))
    two_att = _to_int(player.get("2 Points Attempted"))
    three_made = _to_int(player.get("3 Points Made"))
    three_att = _to_int(
        player.get("3 Points Atttempted") or player.get("3 Points Attempted")
    )
    ft_made = _to_int(player.get("Free Throws Made"))
    ft_att = _to_int(player.get("Free Throws Attempted"))
    return {
        "player": player.get("Player"),
        "player_number": player_number if player_number != "" else None,
        "team": team_name,
        "Minutes": _to_minutes_str(player.get("Minutes")),
        "Points": _to_int(player.get("Points")),
        "2PM": two_made,
        "2PA": two_att,
        "2P%": _percentage_from(player.get("2 Points Percentage"), two_made, two_att),
        "3PM": three_made,
        "3PA": three_att,
        "3P%": _percentage_from(
            player.get("3 Point Percentage"), three_made, three_att
        ),
        "FTM": ft_made,
        "FTA": ft_att,
        "FT%": _percentage_from(player.get("Free Throw Percentage"), ft_made, ft_att),
        "OFF": _to_int(player.get("Offensive Rebounds")),
        "DEF": _to_int(player.get("Defensive Rebounds")),
        "REB": _to_int(player.get("Total Rebounds")),
        "AST": _to_int(player.get("Assists")),
        "STL": _to_int(player.get("Steals")),
        "TO": _to_int(player.get("Turnovers")),
        "BLK": _to_int(player.get("Blocks")),
        "PF": _to_int(player.get("Personal Foul") or player.get("Personal Fouls")),
        "+/-": _to_int(player.get("Plus/Minus")),
        "Index": _to_int(player.get("Index of Success")),
    }


def _normalize_player_baskethotel(
    player: Mapping[str, object], team_name: str | None
) -> dict[str, object]:
    raw_name = text(player.get("Player") or player.get("Pelaaja"))
    extracted_number, cleaned_name = _extract_player_number(raw_name)
    two_made, two_att = _parse_made_attempt(player.get("2P"))
    three_made, three_att = _parse_made_attempt(player.get("3P"))
    ft_made, ft_att = _parse_made_attempt(player.get("1P"))
    rebounds_off = _to_int(player.get("HL"))
    rebounds_def = _to_int(player.get("PL"))
    rebounds_total = _to_int(player.get("LEV"))
    if rebounds_total == 0 and (rebounds_off or rebounds_def):
        rebounds_total = rebounds_off + rebounds_def
    return {
        "player": cleaned_name or raw_name,
        "player_number": extracted_number,
        "team": team_name,
        "Minutes": _to_minutes_str(player.get("MIN")),
        "Points": _to_int(player.get("PIST")),
        "2PM": two_made,
        "2PA": two_att,
        "2P%": _percentage_from(player.get("2P%"), two_made, two_att),
        "3PM": three_made,
        "3PA": three_att,
        "3P%": _percentage_from(player.get("3P%"), three_made, three_att),
        "FTM": ft_made,
        "FTA": ft_att,
        "FT%": _percentage_from(player.get("1P%"), ft_made, ft_att),
        "OFF": rebounds_off,
        "DEF": rebounds_def,
        "REB": rebounds_total,
        "AST": _to_int(player.get("S")),
        "STL": _to_int(player.get("R")),
        "TO": _to_int(player.get("M")),
        "BLK": _to_int(player.get("T+")) or _to_int(player.get("T")),
        "PF": _to_int(player.get("V-")) or _to_int(player.get("V")),
        "+/-": _to_int(player.get("+/-")),
        "Index": _to_int(player.get("TEH")),
    }


def normalize_boxscore(
    boxscore: Mapping[str, object], source: str | None = None
) -> NormalizedBoxScore:
    # A provider-tagged response takes precedence over legacy callers' defaults.
    source = text(boxscore.get("source")) or source
    raw_teams = records(boxscore.get("teams"))
    teams: list[NormalizedTeam] = []
    for team in raw_teams:
        team_name = text(team.get("team_name"))
        players = records(team.get("players"))
        detected_source = source or _detect_source(players)
        if detected_source == "baskethotel":
            normalized_players = [
                _normalize_player_baskethotel(player, team_name) for player in players
            ]
        else:
            normalized_players = [
                _normalize_player_genius(player, team_name) for player in players
            ]
        if detected_source == "sportradar":
            normalized_players = [
                {
                    **normalized,
                    **{
                        key: raw[key]
                        for key in ("player_id", "starter", "played")
                        if key in raw
                    },
                }
                for normalized, raw in zip(normalized_players, players)
            ]

        totals: Mapping[str, object] = _totals(normalized_players)

        if detected_source != "baskethotel" and team.get("totals"):
            reported = _normalize_player_genius(fields(team["totals"]), team_name)
            totals = {key: reported[key] for key in totals}

        teams.append(
            {
                "team_name": team_name,
                "players": normalized_players,
                "totals": totals,
            }
        )

    match_totals = _totals([fields(team["totals"]) for team in teams])

    return {
        "source": source
        or (
            _detect_source(records(raw_teams[0].get("players")))
            if raw_teams
            else "unknown"
        ),
        "teams": teams,
        "match_totals": match_totals,
    }


def _totals(rows: Sequence[Mapping[str, object]]) -> dict[str, int | float]:
    """Aggregate the legacy export's numeric fields in one place."""
    values: dict[str, int | float] = {
        key: sum(_to_int(row.get(key)) for row in rows)
        for key in (
            "Points",
            "2PM",
            "2PA",
            "3PM",
            "3PA",
            "FTM",
            "FTA",
            "OFF",
            "DEF",
            "REB",
            "AST",
            "STL",
            "TO",
            "BLK",
            "PF",
            "Index",
        )
    }
    for prefix in ("2P", "3P", "FT"):
        values[prefix + "%"] = _percentage_from(
            None, int(values[prefix + "M"]), int(values[prefix + "A"])
        )
    return values
