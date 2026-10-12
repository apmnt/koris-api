from pathlib import Path
from typing import Any
from urllib.request import OpenerDirector

import httpx
import pytest
import requests

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
def playbyplay_2701971() -> dict[str, Any]:
    fixture = Path(__file__).parent / "fixtures/genius_sports/playbyplay_2701971.html"
    html = fixture.read_text(encoding="utf-8")
    return GeniusSportsParser.parse_playbyplay_html(html)


@pytest.fixture(autouse=True)
def block_network_in_offline_tests(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A unit test must use a fixture or fake, never silently hit a live API."""
    if request.node.get_closest_marker("integration"):
        return

    def unexpected_network(*args: object, **kwargs: object) -> None:
        pytest.fail(
            "Offline test attempted an HTTP request; inject a fake or mark integration"
        )

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_network)
    monkeypatch.setattr(httpx.Client, "send", unexpected_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", unexpected_network)
    monkeypatch.setattr(OpenerDirector, "open", unexpected_network)
