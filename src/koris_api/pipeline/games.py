"""Acquisition boundary and pure adapters for canonical and legacy consumers."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast

import requests
from bs4 import BeautifulSoup, Tag

from ..baskethotel_api import BasketHotelAPI
from ..baskethotel_parser import BasketHotelParser
from ..boxscore_normalizer import normalize_boxscore
from ..converters import (
    from_baskethotel,
    from_genius,
    from_genius_livestats,
    from_sportradar,
    validate_game,
)
from ..data import fields
from ..genius_livestats_parser import parse_boxscore as parse_livestats_boxscore
from ..genius_livestats_parser import parse_playbyplay as parse_livestats_playbyplay
from ..genius_livestats_parser import parse_shot_chart
from ..genius_parser import GeniusSportsParser
from ..schema.koris.v1.game_pb2 import Game
from ..sportradar_parser import parse_boxscore as parse_sportradar_boxscore
from ..sportradar_parser import parse_playbyplay as parse_sportradar_playbyplay
from .cache import CachedTransport
from .planning import plan_requests
from .transport import RequestsTransport, RetryPolicy, Transport
from .types import (
    Component,
    FetchFailure,
    FetchOutcome,
    GameOutcome,
    GameRef,
    JSONObject,
    Provider,
    Request,
)

# Dynamic historical parser shapes are confined to this compatibility boundary.
LegacyObject = dict[str, object]
T = TypeVar("T")
R = TypeVar("R")


def run_batch(
    items: Sequence[T],
    work: Callable[[T], R],
    *,
    max_workers: int = 5,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[R, ...]:
    """Bound concurrency while preserving input order and propagating programming errors."""
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    results: dict[int, R] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(work, item): index for index, item in enumerate(items)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
            if progress is not None:
                progress(len(results), len(items))
    return tuple(results[index] for index in range(len(items)))


def _baskethotel_fetches(
    reference: GameRef, requested: tuple[Component, ...], transport: Transport
) -> tuple[FetchOutcome, ...]:
    header = transport.fetch(plan_requests(reference, requested)[0])
    if header.failure is not None or not isinstance(header.payload, str):
        return (header,)
    api = BasketHotelAPI()
    state = api.extract_state(header.payload)
    if not state:
        # Some widgets put the complete content directly in the header.
        return (
            replace(header, request=replace(header.request, components=())),
        ) + tuple(
            FetchOutcome(
                Request(c.value, header.request.url, (c,), False), header.payload
            )
            for c in requested
        )
    results = [replace(header, request=replace(header.request, components=()))]
    for component in requested:
        part = "boxscore" if component == Component.BOXSCORE else "play-by-play"
        extra: LegacyObject = {
            "request[0][param][season_id]": reference.season_id,
            "request[0][param][league_id]": reference.league_id,
            "request[0][param][template]": "v2",
        }
        request = Request(
            component.value,
            api.build_game_part_url(
                reference.source_id, state, part=part, extra_params=extra
            ),
            (component,),
            False,
        )
        result = transport.fetch(request)
        if component == Component.PLAYBYPLAY and isinstance(result.payload, str):
            html = BasketHotelParser.extract_html_from_response(result.payload)
            pbp_state = api.extract_state(html)
            if pbp_state:
                soup = BeautifulSoup(html, "html.parser")
                filters = {
                    name: ",".join(
                        str(li.get("data-filter-value"))
                        for li in cast(
                            list[Tag],
                            soup.find_all(
                                "li",
                                attrs={
                                    "data-filter": name,
                                    "data-filter-selected": "true",
                                },
                            ),
                        )
                        if li.get("data-filter-value")
                    )
                    for name in ("quarter", "player_a", "player_b", "action")
                }
                params: LegacyObject = {
                    "request[0][param][season_id]": reference.season_id
                }
                params.update(
                    {
                        f"request[0][param][filter][{name}]": value
                        for name, value in filters.items()
                    }
                )
                request = Request(
                    component.value,
                    api.build_game_part_url(
                        reference.source_id,
                        pbp_state,
                        part="play-by-play-actions",
                        container="2-400-play-by-play-actions-container",
                        extra_params=params,
                    ),
                    (component,),
                    False,
                )
                result = transport.fetch(request)
        results.append(result)
    return tuple(results)


def _payloads(fetches: tuple[FetchOutcome, ...]) -> dict[str, JSONObject | str]:
    return {
        fetch.request.name: fetch.payload
        for fetch in fetches
        if fetch.payload is not None and fetch.failure is None
    }


def convert_game(
    reference: GameRef,
    fetches: tuple[FetchOutcome, ...],
    *,
    basketfi: JSONObject | None = None,
) -> Game:
    """Pure conversion; only successful raw payloads reach source adapters."""
    payloads = _payloads(fetches)

    def object_for(name: str) -> JSONObject | None:
        value = payloads.get(name)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"{name} must be a JSON object")
        return value

    if reference.provider == Provider.SPORTRADAR:
        return from_sportradar(
            boxscore=object_for("statistics"),
            playbyplay=object_for("pbp"),
            basketfi=basketfi,
        )
    if reference.provider == Provider.GENIUS:
        data = object_for("livestats")
        if data is None:
            raise ValueError("LiveStats response is unavailable")
        return from_genius_livestats(
            data,
            match_id=reference.source_id,
            basketfi=basketfi,
            regulation_periods=reference.regulation_periods,
        )
    if reference.provider == Provider.GENIUS_HTML:
        if "livestats" in payloads:
            data = object_for("livestats")
            if data is None:
                raise ValueError("LiveStats response is unavailable")
            return from_genius_livestats(
                data,
                match_id=reference.source_id,
                basketfi=basketfi,
                regulation_periods=reference.regulation_periods,
            )
        return from_genius(
            match_id=reference.source_id,
            boxscore=payloads.get("boxscore"),
            playbyplay=payloads.get("playbyplay"),
            basketfi=basketfi,
            regulation_periods=reference.regulation_periods,
        )
    if reference.league_id is None or reference.season_id is None:
        raise ValueError("BasketHotel league and season context is unavailable")
    return from_baskethotel(
        game_id=reference.source_id,
        league_id=reference.league_id,
        season=reference.season_id,
        header=payloads.get("header"),
        boxscore=payloads.get("boxscore"),
        playbyplay=payloads.get("playbyplay"),
        regulation_periods=reference.regulation_periods,
        clock_mode=reference.clock_mode,
    )


def legacy_components(
    reference: GameRef, fetches: tuple[FetchOutcome, ...]
) -> LegacyObject:
    """Compatibility export, separate from canonical conversion and acquisition."""
    payloads = _payloads(fetches)
    output: LegacyObject = {}
    if not payloads:
        return output
    if reference.provider == Provider.SPORTRADAR:
        if "statistics" in payloads:
            output["boxscore"] = parse_sportradar_boxscore(
                fields(
                    fields(payloads["statistics"]).get("data", payloads["statistics"])
                )
            )
        if "pbp" in payloads:
            pbp = parse_sportradar_playbyplay(
                fields(fields(payloads["pbp"]).get("data", payloads["pbp"]))
            )
            output["playbyplay"] = pbp
            output["shot_chart"] = {
                "source": "sportradar",
                "shots": [
                    dict(e)
                    for e in pbp["events"]
                    if e["event_type"] in {"2pt", "3pt", "freethrow"}
                ],
                "match_info": pbp["match_info"],
            }
    elif reference.provider in {Provider.GENIUS, Provider.GENIUS_HTML}:
        if "livestats" in payloads:
            data = dict(fields(payloads["livestats"]))
            output["boxscore"] = parse_livestats_boxscore(data)
            output["playbyplay"] = parse_livestats_playbyplay(data)
            output["shot_chart"] = parse_shot_chart(reference.source_id, data)
        if "boxscore" in payloads:
            output["boxscore"] = GeniusSportsParser.parse_boxscore_html(
                str(payloads["boxscore"])
            )
        if "playbyplay" in payloads:
            output["playbyplay"] = GeniusSportsParser.parse_playbyplay_html(
                str(payloads["playbyplay"])
            )
    else:
        header = fields(
            BasketHotelParser.parse_game_html(
                BasketHotelParser.extract_html_from_response(
                    str(payloads.get("header", ""))
                )
            )
        )
        if "boxscore" in payloads:
            box = BasketHotelParser.parse_boxscore_html(
                BasketHotelParser.extract_html_from_response(str(payloads["boxscore"]))
            )
            box["game_info"] = header.get("game_info", {})
            box["game_teams"] = header.get("teams", {})
            output["boxscore"] = box
        if "playbyplay" in payloads:
            raw = BasketHotelParser.parse_playbyplay_html(
                BasketHotelParser.extract_html_from_response(
                    str(payloads["playbyplay"])
                )
            )
            raw["game_teams"] = header.get("teams", {})
            raw["score"] = header.get("score", {})
            teams = {
                str(fields(fields(header.get("teams")).get(side)).get("name")): str(
                    index
                )
                for index, side in enumerate(("home", "away"), 1)
                if fields(fields(header.get("teams")).get(side)).get("name")
            }
            output["playbyplay"] = (
                BasketHotelParser.normalize_playbyplay_to_genius_format(
                    raw, team_map=teams
                )
            )
    return output


def acquire_game(
    reference: GameRef,
    components: Iterable[Component],
    *,
    transport: Transport,
    basketfi: JSONObject | None = None,
) -> GameOutcome:
    requested = tuple(dict.fromkeys(components))
    supported = tuple(
        c
        for c in requested
        if reference.provider != Provider.BASKETHOTEL or c != Component.SHOTS
    )
    try:
        fetches = (
            _baskethotel_fetches(reference, supported, transport)
            if reference.provider == Provider.BASKETHOTEL and supported
            else tuple(
                transport.fetch(request)
                for request in plan_requests(reference, supported)
            )
        )
    except ValueError as exc:
        request = Request("unsupported", "", requested)
        return GameOutcome(
            reference,
            requested,
            (FetchOutcome(request, failure=FetchFailure("unsupported", str(exc))),),
        )
    if reference.provider == Provider.BASKETHOTEL and Component.SHOTS in requested:
        fetches += (
            FetchOutcome(
                Request("shot_chart", "", (Component.SHOTS,)),
                failure=FetchFailure(
                    "unsupported", "BasketHotel does not provide a supported shot chart"
                ),
            ),
        )
    # Check requested component presence, not just HTTP success.
    checked: list[FetchOutcome] = []
    for fetch in fetches:
        if fetch.failure is None and fetch.request.components:
            try:
                parsed = legacy_components(
                    reference,
                    tuple(f for f in fetches if f.request.name == "header") + (fetch,)
                    if reference.provider == Provider.BASKETHOTEL
                    else (fetch,),
                )
                for component in fetch.request.components:
                    value = fields(parsed.get(component.value))
                    if (
                        not value
                        or (component == Component.BOXSCORE and not value.get("teams"))
                        or (
                            component == Component.PLAYBYPLAY
                            and not value.get("events")
                        )
                    ):
                        raise ValueError(
                            f"Response is missing requested {component.value}"
                        )
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                fetch = replace(
                    fetch,
                    failure=FetchFailure(
                        "invalid_payload", str(exc), fetch.request.url
                    ),
                )
        checked.append(fetch)
    fetches = tuple(checked)
    try:
        game = convert_game(reference, fetches, basketfi=basketfi)
        return GameOutcome(
            reference, requested, fetches, game, issues=tuple(validate_game(game))
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return GameOutcome(reference, requested, fetches, conversion_failure=str(exc))


def fetch_game(
    reference: GameRef,
    components: Iterable[Component],
    *,
    basketfi: JSONObject | None = None,
    policy: RetryPolicy | None = None,
    cache_dir: Path | None = None,
) -> GameOutcome:
    """Own one session per game; threads never share mutable session state."""
    with requests.Session() as session:
        transport = RequestsTransport(session, policy)
        if cache_dir is None:
            return acquire_game(
                reference, components, transport=transport, basketfi=basketfi
            )
        cached = CachedTransport(transport, cache_dir)
        outcome = acquire_game(
            reference, components, transport=cached, basketfi=basketfi
        )
        for fetch in outcome.fetches:
            if fetch.failure is not None or (
                reference.provider == Provider.BASKETHOTEL and not outcome.complete
            ):
                cached.invalidate(fetch.request)
        return outcome


def export_legacy(outcome: GameOutcome, original: Mapping[str, object]) -> LegacyObject:
    result = dict(original)
    if outcome.conversion_failure or outcome.issues:
        result["conversion_error"] = outcome.conversion_failure or "; ".join(
            outcome.issues
        )
    else:
        _ = result.pop("conversion_error", None)
    values = legacy_components(outcome.reference, outcome.fetches)
    if outcome.reference.provider == Provider.BASKETHOTEL:
        header = _payloads(outcome.fetches).get("header")
        if isinstance(header, str):
            meta = fields(
                BasketHotelParser.parse_game_html(
                    BasketHotelParser.extract_html_from_response(header)
                )
            )
            info = fields(meta.get("game_info"))
            for field in ("date", "time", "venue"):
                if not result.get(field):
                    result[field] = info.get(field)
            for side in ("home", "away"):
                result[f"{side}_team"] = result.get(f"{side}_team") or fields(
                    fields(meta.get("teams")).get(side)
                ).get("name")
                result[f"{side}_score"] = fields(meta.get("score")).get(side)
            if (
                result.get("home_score") is not None
                and result.get("away_score") is not None
            ):
                result["status"] = "Played"
    errors = {
        Component.BOXSCORE: "advanced_boxscore_error",
        Component.PLAYBYPLAY: "playbyplay_error",
        Component.SHOTS: "shot_chart_error",
    }
    for component in outcome.requested:
        failure = next(
            (
                f.failure
                for f in outcome.fetches
                if component in f.request.components and f.failure is not None
            ),
            None,
        )
        if failure is not None:
            result[errors[component]] = {
                "error_type": failure.kind,
                "error_message": failure.message,
                "status_code": failure.status_code,
                "url": failure.url,
            }
        elif component.value in values:
            value = values[component.value]
            result[component.value] = (
                normalize_boxscore(
                    fields(value), source=outcome.reference.provider.value
                )
                if component == Component.BOXSCORE
                else value
            )
            _ = result.pop(errors[component], None)
    pbp = fields(result.get("playbyplay"))
    possessions = fields(pbp.get("possessions"))
    result["home_number_of_possessions"] = possessions.get("team1_possessions")
    result["away_number_of_possessions"] = possessions.get("team2_possessions")
    return result
