"""HTTP status, source error envelopes, and identity/timeout request contracts."""

from unittest.mock import Mock

import pytest
import requests

from koris_api.basketfi_api import BasketFiAPI


def api_call(method: str) -> dict[str, object]:
    if method == "matches":
        return BasketFiAPI.get_matches("huki2627", "4")
    if method == "match":
        return BasketFiAPI.get_match("001")
    if method == "team":
        return BasketFiAPI.get_team("002", "huki2627", "4")
    return BasketFiAPI.get_category("huki2627", "4")


@pytest.mark.parametrize("method", ["matches", "match", "team", "category"])
def test_api_error_envelope_is_not_an_empty_success(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = Mock()
    response.json.return_value = {"call": {"error": "Invalid competition"}}
    monkeypatch.setattr(
        "koris_api.basketfi_api.requests.get", Mock(return_value=response)
    )
    with pytest.raises(ValueError, match="Invalid competition"):
        api_call(method)
    response.raise_for_status.assert_called_once()


@pytest.mark.parametrize("method", ["matches", "match", "team", "category"])
def test_api_http_error_is_propagated_before_parsing(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("503")
    monkeypatch.setattr(
        "koris_api.basketfi_api.requests.get", Mock(return_value=response)
    )
    with pytest.raises(requests.HTTPError):
        api_call(method)
    response.json.assert_not_called()


def test_matches_preserve_ids_query_and_explicit_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"matches": [{"match_id": "001"}]}
    get = Mock(return_value=response)
    monkeypatch.setattr("koris_api.basketfi_api.requests.get", get)
    result = BasketFiAPI.get_matches(team_id="002")
    assert result["matches"][0]["match_id"] == "001"
    assert get.call_args.kwargs["params"] == {"team_id": "002"}
    assert get.call_args.kwargs["timeout"] == BasketFiAPI.DEFAULT_TIMEOUT
    assert result["_status_code"] == 200


def test_historical_roster_query_includes_requested_season(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Mock()
    response.json.return_value = {"team": {"team_id": "002"}}
    get = Mock(return_value=response)
    monkeypatch.setattr("koris_api.basketfi_api.requests.get", get)
    assert BasketFiAPI.get_team("002", "huki2526", "4")["team"]["team_id"] == "002"
    assert get.call_args.kwargs["params"] == {
        "team_id": "002",
        "competition_id": "huki2526",
        "category_id": "4",
    }


@pytest.mark.parametrize("payload", [[], None, {1: "bad key"}])
def test_api_requires_object_payload(
    payload: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = Mock()
    response.json.return_value = payload
    monkeypatch.setattr(
        "koris_api.basketfi_api.requests.get", Mock(return_value=response)
    )
    with pytest.raises((TypeError, ValueError)):
        BasketFiAPI.get_matches("huki2627", "4")


def test_matches_require_selection_context() -> None:
    with pytest.raises(ValueError, match="team_id"):
        BasketFiAPI.get_matches()
