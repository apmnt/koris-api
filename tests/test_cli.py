"""Command routing and exit status without global argv mutation or live requests."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from koris_api import cli
from koris_api.pipeline.types import BatchReport


@pytest.mark.parametrize("action", ["season", "league"])
@pytest.mark.parametrize("output_format", ["protobuf", "protobuf-json"])
def test_canonical_commands_forward_options(
    action: str, output_format: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    download = Mock(return_value=BatchReport(()))
    monkeypatch.setattr(cli, "download_canonical_" + action, download)
    positional = [action, "4", "huki2627"] if action == "season" else [action, "4"]
    assert (
        cli.main(
            [
                *positional,
                "--format",
                output_format,
                "--playbyplay",
                "--shot-chart",
                "--limit-games",
                "2",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert download.call_args.kwargs["binary"] == (output_format == "protobuf")
    assert download.call_args.kwargs["limit_games"] == 2
    assert download.call_args.args[-1] == (
        cli.Component.PLAYBYPLAY,
        cli.Component.SHOTS,
    )


@pytest.mark.parametrize(
    "season,downloader",
    [
        ("2010-2011", "download_baskethotel_season_boxscores"),
        ("huki2627", "download_matches_with_boxscores"),
    ],
)
@pytest.mark.parametrize("failed", [False, True])
def test_legacy_season_uses_source_route_and_failure_metadata(
    season: str,
    downloader: str,
    failed: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def download(**kwargs: object) -> BatchReport:
        Path(str(kwargs["output_file"])).write_text(
            json.dumps({"metadata": {"conversion_failures": int(failed)}})
        )
        return BatchReport(())

    monkeypatch.setattr(cli, downloader, download)
    assert cli.main(
        ["season", "4", season, "--box-score", "--output-dir", str(tmp_path)]
    ) == int(failed)


def test_metadata_season_reports_missing_output_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli.BasketFiAPI,
        "get_category",
        Mock(return_value={"category": {"seasons": []}}),
    )
    monkeypatch.setattr(cli, "download_season_comprehensive", Mock(return_value=None))
    assert cli.main(["season", "4", "huki2627", "--output-dir", str(tmp_path)]) == 1
    assert "output" in capsys.readouterr().err.lower()


def test_metadata_season_uses_reported_season_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli.BasketFiAPI,
        "get_category",
        Mock(
            return_value={
                "category": {
                    "seasons": [
                        {"competition_id": "huki2627", "season_name": "2026-2027"}
                    ]
                }
            }
        ),
    )
    names: list[object] = []

    def download(**kwargs: object) -> None:
        names.append(kwargs["season_name"])
        Path(str(kwargs["output_file"])).write_text('{"metadata":{"matches_failed":0}}')

    monkeypatch.setattr(cli, "download_season_comprehensive", download)
    assert cli.main(["season", "4", "huki2627", "--output-dir", str(tmp_path)]) == 0
    assert names == ["2026-2027"]


@pytest.mark.parametrize("failed", [False, True])
def test_metadata_league_exit_status(
    failed: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "download_league_comprehensive",
        Mock(return_value={"metadata": {"teams_failed": int(failed)}}),
    )
    assert cli.main(["league", "4", "--output-dir", str(tmp_path)]) == int(failed)


def test_match_defaults_to_boxscore_and_reports_invalid_provider_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fetch = Mock()
    monkeypatch.setattr(cli, "fetch_game", fetch)
    assert (
        cli.main(
            ["match", "123", "--provider", "baskethotel", "--output-dir", str(tmp_path)]
        )
        == 1
    )
    fetch.assert_not_called()
    assert "explicit league and season" in capsys.readouterr().err
