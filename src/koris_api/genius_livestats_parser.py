"""Parse the Genius Sports FIBA LiveStats ``data.json`` feed.

The hosted ``competition/{id}/match/{id}/boxscore`` and ``.../playbyplay`` HTML
pages are scoped to a Genius competition id that rotates between seasons (and is
taken offline once a competition closes), so they cannot be relied on for older
or playoff matches.  The FIBA LiveStats feed at
``https://fibalivestats.dcd.shared.geniussports.com/data/{match_id}/data.json``
is keyed only by the match id and stays available, and it contains the full box
score and play-by-play.

This module converts that feed into the exact intermediate shapes the existing
pipeline already understands:

* :func:`parse_boxscore` returns ``{"match_info", "teams"}`` with genius-style
  player keys, ready for :func:`koris_api.boxscore_normalizer.normalize_boxscore`.
* :func:`parse_playbyplay` returns ``{"match_info", "events", "players",
  "possessions"}`` and reuses
  :meth:`GeniusSportsParser._calculate_possessions` so possession logic stays in
  one place.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .genius_parser import GeniusSportsParser

# data.json team key -> genius box score header key.
_PLAYER_FIELD_MAP = {
    "sPoints": "Points",
    "sTwoPointersMade": "2 Points Made",
    "sTwoPointersAttempted": "2 Points Attempted",
    "sThreePointersMade": "3 Points Made",
    "sThreePointersAttempted": "3 Points Attempted",
    "sFreeThrowsMade": "Free Throws Made",
    "sFreeThrowsAttempted": "Free Throws Attempted",
    "sReboundsOffensive": "Offensive Rebounds",
    "sReboundsDefensive": "Defensive Rebounds",
    "sReboundsTotal": "Total Rebounds",
    "sAssists": "Assists",
    "sSteals": "Steals",
    "sTurnovers": "Turnovers",
    "sBlocks": "Blocks",
    "sFoulsPersonal": "Personal Foul",
    "sPlusMinusPoints": "Plus/Minus",
    "eff_1": "Index of Success",
}

# Regulation games have four periods; anything beyond that is overtime.
_REGULATION_PERIODS = 4


def _player_display_name(player: Dict[str, Any]) -> Optional[str]:
    first = (player.get("firstName") or "").strip()
    family = (player.get("familyName") or "").strip()
    full = f"{first} {family}".strip()
    if full:
        return full
    name = player.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _shirt_number(player: Dict[str, Any]) -> Any:
    raw = player.get("shirtNumber")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


def _convert_player(player: Dict[str, Any], team_name: Optional[str]) -> Dict[str, Any]:
    converted: Dict[str, Any] = {
        "Player": _player_display_name(player),
        "Shirt Number": _shirt_number(player),
        "Minutes": player.get("sMinutes"),
        "team": team_name,
    }
    for source_key, target_key in _PLAYER_FIELD_MAP.items():
        converted[target_key] = player.get(source_key)
    return converted


def _ordered_players(team: Dict[str, Any]) -> List[Dict[str, Any]]:
    players = team.get("pl")
    if not isinstance(players, dict):
        return []
    # ``pl`` keys are the numeric roster slots; keep them in numeric order so the
    # roster is stable across fetches.
    def sort_key(item: tuple[str, Any]) -> tuple[int, str]:
        key = item[0]
        try:
            return (0, f"{int(key):04d}")
        except (TypeError, ValueError):
            return (1, str(key))

    return [value for _, value in sorted(players.items(), key=sort_key)]


def parse_boxscore(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a ``data.json`` payload into the genius box score intermediate."""
    teams_raw = data.get("tm")
    if not isinstance(teams_raw, dict):
        raise ValueError("data.json missing 'tm' teams object")

    result: Dict[str, Any] = {"match_info": {}, "teams": []}

    home = teams_raw.get("1") or {}
    away = teams_raw.get("2") or {}
    result["match_info"] = {
        "home_team": home.get("name"),
        "away_team": away.get("name"),
        "home_score": home.get("score"),
        "away_score": away.get("score"),
    }

    for team_key in ("1", "2"):
        team = teams_raw.get(team_key)
        if not isinstance(team, dict):
            continue
        team_name = team.get("name")
        players = [
            _convert_player(player, team_name)
            for player in _ordered_players(team)
            if isinstance(player, dict)
        ]
        result["teams"].append(
            {
                "team_name": team_name,
                "players": players,
                "totals": {},
                "coaches": {},
            }
        )

    return result


def _period_label(event: Dict[str, Any]) -> Optional[str]:
    period = event.get("period")
    if period in (None, ""):
        return None
    try:
        period_num = int(period)
    except (TypeError, ValueError):
        return str(period)

    period_type = str(event.get("periodType") or "").upper()
    if period_type == "OVERTIME" or period_num > _REGULATION_PERIODS:
        overtime_index = max(1, period_num - _REGULATION_PERIODS)
        return f"OT{overtime_index}"
    return f"Q{period_num}"


def _event_team(event: Dict[str, Any]) -> str:
    tno = event.get("tno")
    if tno in (None, ""):
        return "0"
    return str(tno)


def _event_action(event: Dict[str, Any]) -> str:
    """Build an action string whose substrings drive the possession logic.

    ``_calculate_possessions`` only inspects the action text for ``"made"``
    (successful 2pt/3pt/freethrow ends the possession) and ``"won"`` (jump ball
    winner gains control), so those tokens must be present.
    """
    action_type = str(event.get("actionType") or "")
    sub_type = str(event.get("subType") or "").strip()
    success = event.get("success")

    if action_type in ("2pt", "3pt", "freethrow"):
        outcome = "made" if success == 1 else "missed"
        return f"{outcome} {sub_type}".strip()
    if action_type == "jumpball":
        outcome = "won" if success == 1 else "lost"
        return f"{outcome} {sub_type}".strip()
    return sub_type or action_type


def parse_playbyplay(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a ``data.json`` payload into the genius play-by-play intermediate."""
    raw_events = data.get("pbp")
    if not isinstance(raw_events, list):
        raise ValueError("data.json missing 'pbp' events list")

    # The feed lists events newest-first; possession logic walks chronologically.
    chronological = list(reversed(raw_events))

    players: List[Dict[str, Any]] = []
    player_index: Dict[tuple[str, str, str], int] = {}
    events: List[Dict[str, Any]] = []

    for event in chronological:
        if not isinstance(event, dict):
            continue
        team = _event_team(event)
        player_name = (event.get("player") or "").strip() or _player_display_name(event)
        player_number = event.get("shirtNumber")
        player_number = (
            str(player_number).strip() if player_number not in (None, "") else None
        )

        player_id: Optional[int] = None
        if player_name or player_number:
            key = (team, player_number or "", player_name or "")
            player_id = player_index.get(key)
            if player_id is None:
                player_id = len(players) + 1
                player_index[key] = player_id
                players.append(
                    {
                        "player_id": player_id,
                        "name": player_name,
                        "number": player_number,
                        "team": team,
                    }
                )

        events.append(
            {
                "event_type": str(event.get("actionType") or ""),
                "period": _period_label(event),
                "team": team,
                "time": event.get("gt"),
                "score": f"{event.get('s1')}-{event.get('s2')}",
                "action": _event_action(event),
                "player_number": player_number,
                "player_name": player_name,
                "player_id": player_id,
            }
        )

    teams_raw = data.get("tm") or {}
    home = teams_raw.get("1") or {}
    away = teams_raw.get("2") or {}

    return {
        "match_info": {
            "home_team": home.get("name"),
            "away_team": away.get("name"),
            "home_score": home.get("score"),
            "away_score": away.get("score"),
        },
        "events": events,
        "players": players,
        "possessions": GeniusSportsParser._calculate_possessions(events),
    }
