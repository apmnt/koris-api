"""Normalize boxscore data across Genius and BasketHotel sources."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import re


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        cleaned = value.replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_minutes_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        if ":" in value:
            return value
        if value.strip().isdigit():
            return f"{int(value.strip())}:00"
        return value
    try:
        minutes = int(float(value))
        return f"{minutes}:00"
    except (TypeError, ValueError):
        return None


def _parse_made_attempt(value: Any) -> tuple[int, int]:
    if value is None:
        return (0, 0)
    if isinstance(value, str) and "-" in value:
        parts = value.split("-", 1)
        if len(parts) == 2:
            return (_to_int(parts[0]), _to_int(parts[1]))
    return (_to_int(value), 0)


def _percentage_from(value: Any, made: int, attempted: int) -> float:
    if value is not None and value != "":
        return _to_float(value)
    if attempted <= 0:
        return 0.0
    return round((made / attempted) * 100.0, 1)


def _detect_source(players: List[Dict[str, Any]]) -> str:
    if not players:
        return "unknown"
    sample = players[0]
    keys = {str(k) for k in sample.keys()}
    if "PIST" in keys or "LEV" in keys:
        return "baskethotel"
    if "Points" in keys or "Total Rebounds" in keys:
        return "genius"
    return "unknown"


def _extract_player_number(name: Optional[str]) -> tuple[Optional[int], Optional[str]]:
    if not name:
        return (None, name)
    match = re.match(r"^#\s*(\d+)\s*(.*)$", str(name).strip())
    if not match:
        return (None, name)
    number = int(match.group(1))
    cleaned = match.group(2).strip() or None
    return (number, cleaned)


def _normalize_player_genius(
    player: Dict[str, Any], team_name: Optional[str]
) -> Dict[str, Any]:
    player_number = player.get("Shirt Number")
    two_made = _to_int(player.get("2 Points Made"))
    two_att = _to_int(player.get("2 Points Attempted"))
    three_made = _to_int(player.get("3 Points Made"))
    three_att = _to_int(player.get("3 Points Atttempted") or player.get("3 Points Attempted"))
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
        "3P%": _percentage_from(player.get("3 Point Percentage"), three_made, three_att),
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
    player: Dict[str, Any], team_name: Optional[str]
) -> Dict[str, Any]:
    raw_name = player.get("Player") or player.get("Pelaaja")
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
    boxscore: Dict[str, Any], source: Optional[str] = None
) -> Dict[str, Any]:
    teams = []
    for team in boxscore.get("teams", []):
        team_name = team.get("team_name")
        players = team.get("players", []) or []
        detected_source = source or _detect_source(players)
        if detected_source == "baskethotel":
            normalized_players = [
                _normalize_player_baskethotel(player, team_name) for player in players
            ]
        else:
            normalized_players = [
                _normalize_player_genius(player, team_name) for player in players
            ]

        totals = {
            "Points": sum(p["Points"] for p in normalized_players),
            "2PM": sum(p["2PM"] for p in normalized_players),
            "2PA": sum(p["2PA"] for p in normalized_players),
            "2P%": _percentage_from(
                None,
                sum(p["2PM"] for p in normalized_players),
                sum(p["2PA"] for p in normalized_players),
            ),
            "3PM": sum(p["3PM"] for p in normalized_players),
            "3PA": sum(p["3PA"] for p in normalized_players),
            "3P%": _percentage_from(
                None,
                sum(p["3PM"] for p in normalized_players),
                sum(p["3PA"] for p in normalized_players),
            ),
            "FTM": sum(p["FTM"] for p in normalized_players),
            "FTA": sum(p["FTA"] for p in normalized_players),
            "FT%": _percentage_from(
                None,
                sum(p["FTM"] for p in normalized_players),
                sum(p["FTA"] for p in normalized_players),
            ),
            "OFF": sum(p["OFF"] for p in normalized_players),
            "DEF": sum(p["DEF"] for p in normalized_players),
            "REB": sum(p["REB"] for p in normalized_players),
            "AST": sum(p["AST"] for p in normalized_players),
            "STL": sum(p["STL"] for p in normalized_players),
            "TO": sum(p["TO"] for p in normalized_players),
            "BLK": sum(p["BLK"] for p in normalized_players),
            "PF": sum(p["PF"] for p in normalized_players),
            "Index": sum(p["Index"] for p in normalized_players),
        }

        teams.append(
            {
                "team_name": team_name,
                "players": normalized_players,
                "totals": totals,
            }
        )

    match_totals = {
        "Points": sum(team["totals"]["Points"] for team in teams),
        "2PM": sum(team["totals"]["2PM"] for team in teams),
        "2PA": sum(team["totals"]["2PA"] for team in teams),
        "2P%": _percentage_from(
            None,
            sum(team["totals"]["2PM"] for team in teams),
            sum(team["totals"]["2PA"] for team in teams),
        ),
        "3PM": sum(team["totals"]["3PM"] for team in teams),
        "3PA": sum(team["totals"]["3PA"] for team in teams),
        "3P%": _percentage_from(
            None,
            sum(team["totals"]["3PM"] for team in teams),
            sum(team["totals"]["3PA"] for team in teams),
        ),
        "FTM": sum(team["totals"]["FTM"] for team in teams),
        "FTA": sum(team["totals"]["FTA"] for team in teams),
        "FT%": _percentage_from(
            None,
            sum(team["totals"]["FTM"] for team in teams),
            sum(team["totals"]["FTA"] for team in teams),
        ),
        "OFF": sum(team["totals"]["OFF"] for team in teams),
        "DEF": sum(team["totals"]["DEF"] for team in teams),
        "REB": sum(team["totals"]["REB"] for team in teams),
        "AST": sum(team["totals"]["AST"] for team in teams),
        "STL": sum(team["totals"]["STL"] for team in teams),
        "TO": sum(team["totals"]["TO"] for team in teams),
        "BLK": sum(team["totals"]["BLK"] for team in teams),
        "PF": sum(team["totals"]["PF"] for team in teams),
        "Index": sum(team["totals"]["Index"] for team in teams),
    }

    return {
        "source": source
        or _detect_source(boxscore.get("teams", [{}])[0].get("players", [])),
        "teams": teams,
        "match_totals": match_totals,
    }
