"""Season identifiers; calendar input is explicit for deterministic callers."""

from datetime import date


def season_id_for_date(today: date) -> str:
    start = today.year if today.month >= 7 else today.year - 1
    return f"huki{start % 100:02d}{(start + 1) % 100:02d}"


def current_season_id() -> str:
    return season_id_for_date(date.today())
