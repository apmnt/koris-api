"""Pure provider resolution, request planning, and resume decisions."""

from collections.abc import Iterable, Mapping
from urllib.parse import urlencode

from ..sportradar_api import is_sportradar_id
from .types import JSON, Component, GameRef, Provider, Request


def resolve_reference(
    source_id: str,
    *,
    provider: Provider | None = None,
    competition_id: str | None = None,
    league_id: str | None = None,
    season_id: str | None = None,
    clock_mode: str | None = None,
    regulation_periods: int = 4,
) -> GameRef:
    if not source_id.strip():
        raise ValueError("A source game ID is required")
    if regulation_periods <= 0:
        raise ValueError("regulation_periods must be positive")
    if clock_mode not in (None, "remaining"):
        raise ValueError("Only verified remaining game clocks are supported")
    selected = provider or (
        Provider.SPORTRADAR if is_sportradar_id(source_id) else Provider.GENIUS
    )
    if selected == Provider.SPORTRADAR and not is_sportradar_id(source_id):
        raise ValueError("Sportradar requires a fixture UUID")
    if selected == Provider.BASKETHOTEL and (not league_id or not season_id):
        raise ValueError("BasketHotel requires explicit league and season IDs")
    if selected == Provider.GENIUS_HTML and not competition_id:
        raise ValueError("Genius HTML requires an explicit competition ID")
    return GameRef(
        selected,
        source_id,
        competition_id,
        league_id,
        season_id,
        regulation_periods=regulation_periods,
        clock_mode=clock_mode,
    )


def plan_requests(
    reference: GameRef, components: Iterable[Component]
) -> tuple[Request, ...]:
    requested = tuple(dict.fromkeys(components))
    if not requested:
        return ()
    if reference.provider == Provider.UNRESOLVED:
        raise ValueError("Game has no resolved data source")
    if reference.provider == Provider.SPORTRADAR:
        groups = (
            ("statistics", (Component.BOXSCORE,)),
            ("pbp", (Component.PLAYBYPLAY, Component.SHOTS)),
        )
        return tuple(
            Request(
                name,
                "https://embed-api.eui.connect.sportradar.com/v1/embed/322/fixture_detail?"
                + urlencode({"fixtureId": reference.source_id, "sub": name}),
                tuple(c for c in supported if c in requested),
            )
            for name, supported in groups
            if any(c in requested for c in supported)
        )
    if reference.provider == Provider.GENIUS:
        return (
            Request(
                "livestats",
                f"https://fibalivestats.dcd.shared.geniussports.com/data/{reference.source_id}/data.json",
                requested,
            ),
        )
    if reference.provider == Provider.GENIUS_HTML:
        base = f"https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/{reference.competition_id}/match/{reference.source_id}"
        return tuple(
            Request(c.value, f"{base}/{c.value}", (c,), False)
            if c != Component.SHOTS
            else Request(
                "livestats",
                f"https://fibalivestats.dcd.shared.geniussports.com/data/{reference.source_id}/data.json",
                (c,),
            )
            for c in requested
        )
    # BasketHotel state-dependent requests are expanded after reading this header.
    supported = tuple(c for c in requested if c != Component.SHOTS)
    if Component.SHOTS in requested:
        raise ValueError("BasketHotel does not provide a supported shot chart")
    from ..baskethotel_api import BasketHotelAPI

    url = BasketHotelAPI().build_game_url(
        reference.source_id, str(reference.season_id), str(reference.league_id)
    )
    return (Request("header", url, supported, False),)


def missing_components(
    requested: Iterable[Component],
    cached: Mapping[str, JSON],
    *,
    retry_failures: bool = True,
) -> tuple[Component, ...]:
    errors = {
        Component.BOXSCORE: "advanced_boxscore_error",
        Component.PLAYBYPLAY: "playbyplay_error",
        Component.SHOTS: "shot_chart_error",
    }
    return tuple(
        c
        for c in requested
        if (not component_present(c, cached) or bool(cached.get("conversion_error")))
        and (retry_failures or not cached.get(errors[c]))
    )


def component_present(component: Component, row: Mapping[str, JSON]) -> bool:
    value = row.get(component.value)
    if not isinstance(value, dict):
        return False
    if component == Component.BOXSCORE:
        return isinstance(value.get("teams"), list) and bool(value["teams"])
    if component == Component.PLAYBYPLAY:
        return isinstance(value.get("events"), list) and bool(value["events"])
    return isinstance(value.get("shots"), list)
