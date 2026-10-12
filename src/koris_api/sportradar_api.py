"""HTTP boundary for the public widget used by tulospalvelu.basket.fi."""

from typing import Any
from uuid import UUID

import requests


def is_sportradar_id(match_id: str) -> bool:
    """Current Basket.fi Sportradar fixtures use UUIDs; Genius uses numeric IDs."""
    try:
        UUID(str(match_id))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def fetch_fixture(
    match_id: str,
    sub: str,
    *,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    response = (session or requests).get(
        "https://embed-api.eui.connect.sportradar.com/v1/embed/322/fixture_detail",
        params={"fixtureId": match_id, "sub": sub},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("data") is None:
        raise ValueError("Sportradar response missing fixture data")
    if not isinstance(payload["data"], dict):
        raise TypeError("Sportradar fixture data must be an object")
    return payload["data"]
