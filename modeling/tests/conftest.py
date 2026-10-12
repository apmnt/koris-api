import json
from pathlib import Path
from typing import cast
from urllib.request import OpenerDirector

import httpx
import pytest
import requests

from koris_api import Component, Season, SeasonData
from koris_api.converters import from_genius_livestats


@pytest.fixture
def dataset() -> SeasonData:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "tests/fixtures/genius_sports/livestats_2514938.json"
    )
    raw = cast(dict[str, object], json.loads(fixture.read_text()))
    game = from_genius_livestats(raw, match_id="2514938")
    return SeasonData(
        Season("4", "huki2526", "2025-2026", "Korisliiga"),
        (game,),
        (),
        1,
        tuple(Component),
        "2026-10-12T00:00:00+00:00",
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_network(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline modeling test attempted an HTTP request")

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_network)
    monkeypatch.setattr(httpx.Client, "send", unexpected_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", unexpected_network)
    monkeypatch.setattr(OpenerDirector, "open", unexpected_network)
