"""Failure and persistence contracts for the I/O boundary."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from koris_api.pipeline.cache import CachedTransport
from koris_api.pipeline.export import write_bytes, write_json
from koris_api.pipeline.transport import RequestsTransport, RetryPolicy, validate_json
from koris_api.pipeline.types import FetchFailure, FetchOutcome, Request


@pytest.mark.parametrize(
    "error", [requests.Timeout("timeout"), requests.ConnectionError("offline")]
)
def test_connection_failures_exhaust_bounded_retries(
    error: requests.RequestException,
) -> None:
    client = Mock()
    client.get.side_effect = error
    waits: list[float] = []
    outcome = RequestsTransport(client, RetryPolicy(2, 7, 0.1), waits.append).fetch(
        Request("data", "https://example.test", ())
    )
    assert client.get.call_count == 3
    assert waits == [0.1, 0.2]
    assert outcome.failure is not None and outcome.failure.retryable
    assert outcome.failure.kind == "transport"
    assert all(call.kwargs["timeout"] == 7 for call in client.get.call_args_list)


@pytest.mark.parametrize("body", [b"bad json", b"null", b"[]", b'"text"', b'{"x":NaN}'])
def test_invalid_json_is_not_retried_or_exported(body: bytes) -> None:
    response = requests.Response()
    response.status_code = 200
    response._content = body
    client = Mock()
    client.get.return_value = response
    outcome = RequestsTransport(client).fetch(
        Request("data", "https://example.test", ())
    )
    assert client.get.call_count == 1
    assert outcome.payload is None
    assert outcome.failure is not None and outcome.failure.kind == "invalid_payload"
    assert not outcome.failure.retryable


@pytest.mark.parametrize(
    "value", [float("inf"), float("nan"), {1: "value"}, {"nested": [object()]}]
)
def test_json_validation_rejects_non_json_values(value: object) -> None:
    with pytest.raises(ValueError):
        validate_json(value)


@pytest.mark.parametrize(
    "record",
    [
        "broken",
        '{"url":"different","payload":{}}',
        '{"url":"https://example.test","payload":[]}',
    ],
)
def test_corrupt_checkpoint_refetches_and_repairs(tmp_path: Path, record: str) -> None:
    request = Request("data", "https://example.test", ())
    remote = Mock()
    remote.fetch.return_value = FetchOutcome(request, {"ok": True})
    cached = CachedTransport(remote, tmp_path)
    cached.fetch(request)
    path = next(tmp_path.glob("*.json"))
    path.write_text(record)
    assert cached.fetch(request).payload == {"ok": True}
    assert remote.fetch.call_count == 2
    assert json.loads(path.read_text())["payload"] == {"ok": True}


def test_failed_response_is_never_checkpointed(tmp_path: Path) -> None:
    request = Request("data", "https://example.test", ())
    remote = Mock()
    remote.fetch.return_value = FetchOutcome(
        request, failure=FetchFailure("http", "not found", status_code=404)
    )
    cached = CachedTransport(remote, tmp_path)
    cached.fetch(request)
    cached.fetch(request)
    assert remote.fetch.call_count == 2
    assert list(tmp_path.iterdir()) == []


def test_serialization_failure_preserves_existing_output(tmp_path: Path) -> None:
    target = tmp_path / "game.json"
    target.write_text('{"old":true}')
    with pytest.raises(ValueError):
        write_json(target, {"bad": float("nan")})
    assert target.read_text() == '{"old":true}'
    assert list(tmp_path.iterdir()) == [target]


def test_failed_atomic_replace_preserves_output_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "game.json"
    target.write_bytes(b"old")

    def fail_replace(*args: object) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr("koris_api.pipeline.export.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        write_bytes(target, b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]
