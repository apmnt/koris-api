"""Possession analysis returns fresh events and leaves caller data untouched."""

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from itertools import islice
from typing import TypedDict

from ..data import text


class Possession(TypedDict):
    possession_number: int
    team: str
    start_time: str | None
    start_time_seconds: int | None
    start_period: str | None
    end_time: str | None
    end_time_seconds: int | None
    end_period: str | None
    duration_seconds: int | None
    events: list[dict[str, object]]


class PossessionSummary(TypedDict):
    total_possessions: int
    team1_possessions: int
    team2_possessions: int
    team1_avg_possession_length_seconds: float
    team2_avg_possession_length_seconds: float
    possessions_list: list[Possession]


def enrich_possessions(
    events: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], PossessionSummary]:
    owned = [deepcopy(dict(event)) for event in events]
    summary = _calculate_owned(owned)
    return owned, summary


def calculate_possessions(events: Sequence[Mapping[str, object]]) -> PossessionSummary:
    return enrich_possessions(events)[1]


def _calculate_owned(events: list[dict[str, object]]) -> PossessionSummary:
    """
    Calculate possessions from play-by-play events.

    A possession is when one team has control of the ball. It ends when:
    - The other team gains control
    - The ball becomes dead after a made field goal or last free throw
    - The period ends

    Args:
        events: List of play-by-play events

    Returns:
        Dictionary with possession statistics and detailed possession list
    """
    possessions: list[Possession] = []
    current_possession: Possession | None = None

    def parse_time(time_str: str | None) -> int | None:
        if time_str and ":" in time_str:
            try:
                parts = time_str.split(":")
                if len(parts) >= 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except (ValueError, IndexError):
                return None
        return None

    def period_start_clock(period_label: str | None) -> str | None:
        if not period_label:
            return None
        upper = period_label.upper()
        if re.fullmatch(r"[PQ]\d+", upper):
            return "10:00:00"
        if upper.startswith("OT"):
            return "05:00:00"
        return None

    periods_with_possessions: set[str] = set()

    def start_possession(
        team_id: str,
        time_str: str | None,
        time_seconds: int | None,
        period: str | None,
    ) -> Possession:
        normalized_start_time = time_str
        normalized_start_seconds = time_seconds

        default_clock = period_start_clock(period)
        if period and period not in periods_with_possessions and default_clock:
            normalized_start_time = default_clock
            normalized_start_seconds = parse_time(default_clock)
            periods_with_possessions.add(period)

        return {
            "possession_number": len(possessions) + 1,
            "team": team_id,
            "start_time": normalized_start_time,
            "start_time_seconds": normalized_start_seconds,
            "start_period": period,
            "end_time": None,
            "end_time_seconds": None,
            "end_period": None,
            "duration_seconds": None,
            "events": [],
        }

    def end_possession(
        possession: Possession,
        time_str: str | None,
        time_seconds: int | None,
        period: str | None,
        event_payload: dict[str, object],
    ) -> None:
        possession["end_time"] = time_str
        possession["end_time_seconds"] = time_seconds
        possession["end_period"] = period
        if not possession.get("events"):
            payload = dict(event_payload)
            payload["possession_event_role"] = "start_end"
            possession["events"].append(payload)
        else:
            last = possession["events"][-1]
            prev_role = last.get("possession_event_role")
            if prev_role == "start":
                last["possession_event_role"] = "start_end"
            elif prev_role is None:
                last["possession_event_role"] = "end"

        if (
            possession["start_time_seconds"] is not None
            and time_seconds is not None
            and possession["start_period"] == period
        ):
            duration = possession["start_time_seconds"] - time_seconds
            possession["duration_seconds"] = max(0, duration)
        elif possession["start_time_seconds"] is not None and time_seconds is not None:
            possession["duration_seconds"] = None

    def find_next_event_with_same_time(
        start_idx: int, time_str: str | None, period: str | None
    ) -> dict[str, object] | None:
        if time_str is None:
            return None
        future = events[start_idx + 1] if start_idx + 1 < len(events) else None
        if (
            future is not None
            and future.get("time") == time_str
            and future.get("period") == period
        ):
            return future
        return None

    def has_free_throw_ahead(
        start_idx: int, team_id: str, time_str: str | None, period: str | None
    ) -> bool:
        if time_str is None:
            return False
        for future in islice(events, start_idx + 1, None):
            future_time = future.get("time")
            future_period = future.get("period")
            if future_time != time_str or future_period != period:
                return False
            if (
                future.get("event_type") == "freethrow"
                and future.get("team") == team_id
            ):
                return True
        return False

    def add_event_to_possession(
        possession: Possession,
        payload: dict[str, object],
        role: str | None = None,
    ) -> None:
        event_copy = dict(payload)
        if role is None and not possession["events"]:
            role = "start"
        event_copy["possession_event_role"] = role
        possession["events"].append(event_copy)

    non_possession_start_events = {
        "substitution",
        "timeout",
        "assist",
        "block",
        "foul",
        "foulon",
    }

    for idx, event in enumerate(events):
        event_type = text(event.get("event_type", ""))
        team = text(event.get("team", "")) or ""
        time_str = text(event.get("time", ""))
        period = text(event.get("period", ""))
        action = text(event.get("action", ""))
        player_id = event.get("player_id")
        score = event.get("score")
        action_lower = (action or "").lower()
        event_payload = {
            "event_type": event_type,
            "time": time_str,
            "action": action,
            "team": team,
            "player_id": player_id,
            "player_name": event.get("player_name"),
            "player_number": event.get("player_number"),
            "score": score,
        }

        # Period/game marker closes any active possession.
        # Some feeds emit non-standard end-of-period clocks (for example
        # "00:00:60"), so we do not require an exact "00:00" match here.
        if event_type in ["period", "game"]:
            if current_possession:
                marker_time = "00:00:00"
                marker_period = current_possession.get("start_period") or period
                end_possession(
                    current_possession,
                    marker_time,
                    parse_time(marker_time),
                    marker_period,
                    event_payload,
                )
                possessions.append(current_possession)
                current_possession = None
            continue

        # Skip neutral events
        if team in ["0", None, ""]:
            continue

        time_seconds = parse_time(time_str)

        if current_possession is None:
            # Dead-ball/non-possession events should not create a possession.
            if event_type in non_possession_start_events:
                continue

            if event_type == "jumpball":
                if "won" in action_lower:
                    current_possession = start_possession(
                        team, time_str, time_seconds, period
                    )
                    event["possession_number"] = current_possession["possession_number"]
                    event["possession_team"] = current_possession["team"]
                    add_event_to_possession(
                        current_possession, event_payload, role="start"
                    )
                continue
            current_possession = start_possession(team, time_str, time_seconds, period)
        elif event_type == "jumpball":
            # Ignore non-winning jump ball entries during a possession.
            if "won" not in action_lower:
                continue

        current_team = current_possession["team"] if current_possession else None

        # Determine if possession changes
        possession_change = False
        new_team = None
        event_belongs_to_new_possession = False

        # Made field goal - other team gets possession unless an and-1 follows
        if event_type in ["2pt", "3pt"] and "made" in action_lower:
            if current_team == team and not has_free_throw_ahead(
                idx, team, time_str, period
            ):
                possession_change = True
                new_team = "1" if team == "2" else "2"

        # Free throws: only last made FT ends possession
        elif event_type == "freethrow" and "made" in action_lower:
            if current_team == team and not has_free_throw_ahead(
                idx, team, time_str, period
            ):
                next_event = find_next_event_with_same_time(idx, time_str, period)
                retained_events = {
                    "2pt",
                    "3pt",
                    "turnover",
                    "rebound",
                    "jumpball",
                    "steal",
                }
                if (
                    next_event
                    and next_event.get("team") == team
                    and (next_event.get("event_type") in retained_events)
                ):
                    possession_change = False
                else:
                    possession_change = True
                    new_team = "1" if team == "2" else "2"

        # Rebound: defensive rebound changes possession, offensive does not
        elif event_type == "rebound":
            if current_team and team != current_team:
                possession_change = True
                new_team = team
                event_belongs_to_new_possession = True

        # Turnover - other team gets possession
        elif event_type == "turnover":
            if current_team == team:
                possession_change = True
                new_team = "1" if team == "2" else "2"

        # Steal - stealing team gets possession
        elif event_type == "steal":
            if current_team and team != current_team:
                possession_change = True
                new_team = team
                event_belongs_to_new_possession = True

        # Jump ball - team in event gains control
        elif (
            event_type == "jumpball"
            and "won" in action_lower
            and current_team
            and team != current_team
        ):
            possession_change = True
            new_team = team
            event_belongs_to_new_possession = True

        # Foul does not automatically change possession

        if current_possession and not event_belongs_to_new_possession:
            event["possession_number"] = current_possession["possession_number"]
            event["possession_team"] = current_possession["team"]
            add_event_to_possession(current_possession, event_payload)

        # Handle possession change
        if possession_change and new_team and new_team != "0":
            # End current possession
            if current_possession:
                end_possession(
                    current_possession, time_str, time_seconds, period, event_payload
                )
                possessions.append(current_possession)

            if event_belongs_to_new_possession:
                current_possession = start_possession(
                    new_team, time_str, time_seconds, period
                )
                event["possession_number"] = current_possession["possession_number"]
                event["possession_team"] = current_possession["team"]
                add_event_to_possession(current_possession, event_payload, role="start")
            else:
                # Start next possession immediately using the change-of-control
                # event as the possession start marker.
                current_possession = start_possession(
                    new_team, time_str, time_seconds, period
                )
                add_event_to_possession(current_possession, event_payload, role="start")

    # Close final possession
    if current_possession:
        possessions.append(current_possession)

    # Calculate statistics
    team1_possessions = [p for p in possessions if p["team"] == "1"]
    team2_possessions = [p for p in possessions if p["team"] == "2"]

    # Calculate average possession length (only for possessions with duration)
    team1_durations = [
        duration
        for p in team1_possessions
        if (duration := p["duration_seconds"]) is not None
    ]
    team2_durations = [
        duration
        for p in team2_possessions
        if (duration := p["duration_seconds"]) is not None
    ]

    team1_avg_duration = (
        sum(team1_durations) / len(team1_durations) if team1_durations else 0
    )
    team2_avg_duration = (
        sum(team2_durations) / len(team2_durations) if team2_durations else 0
    )

    return {
        "total_possessions": len(possessions),
        "team1_possessions": len(team1_possessions),
        "team2_possessions": len(team2_possessions),
        "team1_avg_possession_length_seconds": round(team1_avg_duration, 2),
        "team2_avg_possession_length_seconds": round(team2_avg_duration, 2),
        "possessions_list": possessions,
    }
