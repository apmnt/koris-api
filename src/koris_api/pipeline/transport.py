"""Owned HTTP sessions and bounded retries; injectable for fixture tests."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

import requests

from .types import JSON, FetchFailure, FetchOutcome, Request


class Transport(Protocol):
    def fetch(self, request: Request) -> FetchOutcome: ...


class HTTPClient(Protocol):
    def get(
        self, url: str, *, timeout: float, headers: dict[str, str]
    ) -> requests.Response: ...


def validate_json(value: object) -> JSON:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [validate_json(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        items = cast(dict[object, object], value)
        if any(not isinstance(key, str) for key in items):
            raise ValueError("JSON object keys must be strings")
        return {str(key): validate_json(item) for key, item in items.items()}
    raise ValueError("Response contains an unsupported JSON value")


@dataclass(frozen=True)
class RetryPolicy:
    retries: int = 3
    timeout: float = 30.0
    backoff: float = 0.6

    def __post_init__(self) -> None:
        if self.retries < 0 or self.timeout <= 0 or self.backoff < 0:
            raise ValueError("Invalid retry policy")


class RequestsTransport:
    def __init__(
        self,
        session: HTTPClient,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session: HTTPClient = session
        self.policy: RetryPolicy = policy or RetryPolicy()
        self.sleep: Callable[[float], None] = sleep

    def fetch(self, request: Request) -> FetchOutcome:
        failure = FetchFailure("transport", "Request did not complete", request.url)
        for attempt in range(self.policy.retries + 1):
            try:
                response = self.session.get(
                    request.url,
                    timeout=self.policy.timeout,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.basket.fi/",
                    },
                )
                response.raise_for_status()
                if request.json:
                    payload = validate_json(cast(object, response.json()))
                    if not isinstance(payload, dict):
                        raise ValueError("Expected a JSON object")
                else:
                    payload = response.text
                return FetchOutcome(request, payload)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                retryable = status == 429 or (status is not None and status >= 500)
                failure = FetchFailure("http", str(exc), request.url, status, retryable)
            except (requests.Timeout, requests.ConnectionError) as exc:
                failure = FetchFailure(
                    "transport", str(exc), request.url, retryable=True
                )
            except requests.RequestException as exc:
                failure = FetchFailure("transport", str(exc), request.url)
            except ValueError as exc:
                failure = FetchFailure("invalid_payload", str(exc), request.url)
            if not failure.retryable or attempt == self.policy.retries:
                break
            self.sleep(self.policy.backoff * (attempt + 1))
        return FetchOutcome(request, failure=failure)
