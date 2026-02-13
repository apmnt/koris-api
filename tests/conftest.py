from pathlib import Path

import pytest

from koris_api.genius_parser import GeniusSportsParser


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test (makes real API calls)",
    )
    config.addinivalue_line(
        "markers",
        "performance: mark test as a performance test (full-season downloads)",
    )


@pytest.fixture(scope="session")
def playbyplay_2701971() -> dict:
    fixture = Path("tests/fixtures/genius_sports/playbyplay_2701971.html")
    html = fixture.read_text(encoding="utf-8")
    return GeniusSportsParser.parse_playbyplay_html(html)
