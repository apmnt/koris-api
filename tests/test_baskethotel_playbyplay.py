from pathlib import Path

from koris_api.baskethotel_parser import BasketHotelParser


def test_baskethotel_playbyplay_parses_events() -> None:
    fixture = Path("tests/fixtures/baskethotel/playbyplay.html")
    html = fixture.read_text(encoding="utf-8")
    parsed = BasketHotelParser.parse_playbyplay_html(html)

    events = parsed.get("events", [])
    assert events

    first = events[0]
    assert first.get("time") == "00:00"
    assert first.get("team")
    assert first.get("players")


def test_baskethotel_playbyplay_normalizes_to_genius_format() -> None:
    fixture = Path("tests/fixtures/baskethotel/playbyplay.html")
    html = fixture.read_text(encoding="utf-8")
    parsed = BasketHotelParser.parse_playbyplay_html(html)
    parsed["game_teams"] = {
        "home": {"name": "Jyväskylä Basketball Academy"},
        "away": {"name": "Tapiolan Honka"},
    }
    parsed["score"] = {"home": 94, "away": 63}
    parsed["game_info"] = {"date": "01.01.2022", "venue": "Test Arena"}

    normalized = BasketHotelParser.normalize_playbyplay_to_genius_format(
        parsed,
        team_map={
            "Jyväskylä Basketball Academy": "1",
            "Tapiolan Honka": "2",
        },
    )

    assert "match_info" in normalized
    assert "events" in normalized
    assert "players" in normalized
    assert normalized["match_info"]["home_team"] == "Jyväskylä Basketball Academy"
