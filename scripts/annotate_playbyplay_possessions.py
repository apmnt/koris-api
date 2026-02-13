#!/usr/bin/env python3
"""
Download or read a Genius Sports play-by-play HTML page and annotate possession ends.

Examples:
  uv run python scripts/annotate_playbyplay_possessions.py \
    --playbyplay-url "https://hosted.dcd.shared.geniussports.com/FBAA/en/competition/42145/match/2701971/playbyplay" \
    --output-dir /tmp/pbp_debug

  uv run python scripts/annotate_playbyplay_possessions.py \
    --input-html tests/fixtures/genius_sports/playbyplay_2701971.html \
    --output-dir /tmp/pbp_debug
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

from koris_api.genius_parser import GeniusSportsParser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Save original play-by-play HTML and create an annotated version with "
            "a divider after each possession-ending event."
        )
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--playbyplay-url",
        help="Full Genius Sports play-by-play URL to download.",
    )
    source_group.add_argument(
        "--input-html",
        help="Path to a local play-by-play HTML file.",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/playbyplay_possession_debug",
        help="Directory where original and annotated HTML files are saved.",
    )
    parser.add_argument(
        "--base-name",
        default=None,
        help=(
            "Base output name without extension. "
            "Defaults to match_<id> when possible."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="HTTP timeout when downloading from URL.",
    )
    parser.add_argument(
        "--prefer-existing-html",
        action="store_true",
        help=(
            "In --playbyplay-url mode, use an existing "
            "<base-name>_original.html from --output-dir if present "
            "instead of downloading again."
        ),
    )
    return parser.parse_args()


def _infer_base_name(playbyplay_url: Optional[str], input_html: Optional[str]) -> str:
    if playbyplay_url:
        match = re.search(r"/match/([^/]+)/playbyplay", playbyplay_url)
        if match:
            return f"playbyplay_{match.group(1)}"
        parsed = urlparse(playbyplay_url)
        tail = Path(parsed.path).name or "playbyplay"
        return f"playbyplay_{tail}"
    if input_html:
        return Path(input_html).stem
    return "playbyplay"


def _download_html(url: str, timeout_seconds: float) -> str:
    response = requests.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    return response.text


def _extract_event_rows_with_team(soup: BeautifulSoup) -> list[Tag]:
    playbyplay_div = soup.find("div", id="playbyplay")
    if not playbyplay_div or not isinstance(playbyplay_div, Tag):
        raise ValueError("Could not find <div id='playbyplay'> in HTML.")

    rows: list[Tag] = []
    for event_div in playbyplay_div.find_all("div", class_="pbpa"):
        if not isinstance(event_div, Tag):
            continue
        pbp_team = event_div.find("div", class_="pbp-team")
        if pbp_team and isinstance(pbp_team, Tag):
            rows.append(event_div)
    return rows


def _normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _event_signature(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _normalize_text(event.get("event_type")),
        _normalize_text(event.get("time")),
        _normalize_text(event.get("team")),
        _normalize_text(event.get("action")),
        event.get("player_id"),
        _normalize_text(event.get("player_name")),
        _normalize_text(event.get("player_number")),
        _normalize_text(event.get("score")),
    )


def _map_possession_end_event_indices(
    parsed_events: list[dict[str, Any]],
    possessions: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    index_to_possessions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    search_start = 0

    parsed_signatures = [_event_signature(event) for event in parsed_events]

    for possession in possessions:
        possession_events = possession.get("events") or []
        if not possession_events:
            continue
        end_event = possession_events[-1]
        end_signature = _event_signature(end_event)

        found_index: Optional[int] = None
        for idx in range(search_start, len(parsed_signatures)):
            if parsed_signatures[idx] == end_signature:
                found_index = idx
                break

        if found_index is None:
            raise ValueError(
                "Could not map possession end event back to parsed event stream: "
                f"{end_signature}"
            )

        index_to_possessions[found_index].append(possession)
        search_start = found_index

    return index_to_possessions


def _divider_label_for_possessions(possessions: list[dict[str, Any]]) -> str:
    if len(possessions) == 1:
        p = possessions[0]
        number = p.get("possession_number")
        start_time = p.get("start_time")
        end_time = p.get("end_time")
        end_event_type = (p.get("events") or [{}])[-1].get("event_type")
        return (
            f"End possession {number}: {start_time} -> {end_time} "
            f"(end event: {end_event_type})"
        )
    numbers = ", ".join(str(p.get("possession_number")) for p in possessions)
    return f"End possessions: {numbers}"


def _annotate_html_with_possession_dividers(
    html: str,
    parsed: dict[str, Any],
) -> tuple[str, int]:
    soup = BeautifulSoup(html, "html.parser")
    event_rows = _extract_event_rows_with_team(soup)
    parsed_events = parsed.get("events") or []
    possessions = parsed.get("possessions", {}).get("possessions_list", []) or []

    if len(event_rows) != len(parsed_events):
        raise ValueError(
            "Parsed events and HTML event rows are not aligned: "
            f"{len(parsed_events)} parsed events vs {len(event_rows)} HTML rows."
        )

    index_to_possessions = _map_possession_end_event_indices(parsed_events, possessions)

    style_tag = soup.new_tag("style")
    style_tag.string = (
        "#playbyplay .pbpa.possession-end {"
        "  position: relative;"
        "  border-bottom: 4px solid #c92a2a !important;"
        "  padding-bottom: 10px !important;"
        "  margin-bottom: 14px !important;"
        "}"
        "#playbyplay .pbpa.possession-end::after {"
        "  content: attr(data-possession-label);"
        "  display: block;"
        "  margin-top: 6px;"
        "  font: 700 12px/1.2 monospace;"
        "  color: #8a1c1c;"
        "  letter-spacing: 0.01em;"
        "}"
    )
    if soup.head and isinstance(soup.head, Tag):
        soup.head.append(style_tag)
    elif soup.body and isinstance(soup.body, Tag):
        soup.body.insert(0, style_tag)

    divider_count = 0
    for idx, row in enumerate(event_rows):
        possessions_ending_here = index_to_possessions.get(idx)
        if not possessions_ending_here:
            continue
        classes = row.get("class", [])
        if "possession-end" not in classes:
            classes.append("possession-end")
            row["class"] = classes
        row["data-possession-label"] = _divider_label_for_possessions(
            possessions_ending_here
        )
        divider_count += 1

    return str(soup), divider_count


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = args.base_name or _infer_base_name(args.playbyplay_url, args.input_html)
    original_html_path = output_dir / f"{base_name}_original.html"
    annotated_html_path = output_dir / f"{base_name}_annotated.html"
    parsed_json_path = output_dir / f"{base_name}_parsed.json"

    if args.playbyplay_url:
        if args.prefer_existing_html and original_html_path.exists():
            html = original_html_path.read_text(encoding="utf-8")
            source_description = (
                f"{original_html_path} (reused for URL {args.playbyplay_url})"
            )
        else:
            html = _download_html(args.playbyplay_url, args.timeout_seconds)
            source_description = args.playbyplay_url
    else:
        input_path = Path(args.input_html)
        html = input_path.read_text(encoding="utf-8")
        source_description = str(input_path)

    parsed = GeniusSportsParser.parse_playbyplay_html(html)
    annotated_html, divider_count = _annotate_html_with_possession_dividers(html, parsed)

    original_html_path.write_text(html, encoding="utf-8")
    annotated_html_path.write_text(annotated_html, encoding="utf-8")
    parsed_json_path.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    total_possessions = (
        parsed.get("possessions", {}).get("total_possessions") or divider_count
    )
    print(f"Source: {source_description}")
    print(f"Original HTML: {original_html_path}")
    print(f"Annotated HTML: {annotated_html_path}")
    print(f"Parsed JSON: {parsed_json_path}")
    print(
        f"Inserted {divider_count} divider lines "
        f"for {total_possessions} possessions."
    )


if __name__ == "__main__":
    main()
