"""Capture live responses into a separate directory for review before updating goldens."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from koris_api.basketfi_api import BasketFiAPI
from koris_api.basketfi_parser import BasketFiParser
from koris_api.pipeline import Component, Provider, fetch_game, resolve_reference
from koris_api.pipeline.export import write_bytes, write_json
from koris_api.seasons import current_season_id


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="/tmp/koris-api-fixtures")
    parser.add_argument("--season-id", default=current_season_id())
    parser.add_argument("--category-id", default="4")
    args = parser.parse_args(argv)
    directory = Path(args.output_dir)
    raw = BasketFiAPI.get_matches(args.season_id, args.category_id)
    played = [
        m
        for m in BasketFiParser.extract_matches(raw)
        if BasketFiParser.is_match_played(m)
        and m.get("match_external_id")
        and m.get("date")
    ]
    if not played:
        raise ValueError(
            "No played game with an external source in the selected season"
        )
    match = max(played, key=lambda row: str(row["date"]))
    metadata = directory / "basketfi"
    write_json(metadata / "matches.json", raw)
    write_json(
        metadata / "match_detail.json", BasketFiAPI.get_match(str(match["match_id"]))
    )
    write_json(
        metadata / "team.json",
        BasketFiAPI.get_team(str(match["team_A_id"]), args.season_id, args.category_id),
    )
    write_json(
        metadata / "category.json",
        BasketFiAPI.get_category(args.season_id, args.category_id),
    )
    references = [
        (resolve_reference(str(match["match_external_id"])), tuple(Component)),
        (resolve_reference("2514938"), tuple(Component)),
        (
            resolve_reference(
                "239490", provider=Provider.BASKETHOTEL, league_id="4", season_id="8589"
            ),
            (Component.BOXSCORE, Component.PLAYBYPLAY),
        ),
    ]
    failed = False
    for reference, components in references:
        outcome = fetch_game(reference, components)
        target = directory / reference.provider.value / reference.source_id
        for fetched in outcome.fetches:
            if fetched.payload is None:
                continue
            if isinstance(fetched.payload, str):
                write_bytes(
                    target / f"{fetched.request.name}.html",
                    fetched.payload.encode("utf-8"),
                )
            else:
                write_json(target / f"{fetched.request.name}.json", fetched.payload)
        write_json(
            target / "report.json",
            {
                "source": reference.key,
                "complete": outcome.complete,
                "issues": outcome.issues,
                "conversion_failure": outcome.conversion_failure,
                "failures": [f.failure.message for f in outcome.fetches if f.failure],
            },
        )
        failed |= not outcome.complete
    print(
        f"Captured responses to {directory}; {'some sources failed' if failed else 'all sources passed'}"
    )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
