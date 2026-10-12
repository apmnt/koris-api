"""Explicit raw-response checkpoints, independent of export filenames."""

import hashlib
import json
from pathlib import Path
from typing import cast

from .export import write_json
from .transport import Transport, validate_json
from .types import FetchOutcome, Request


class CachedTransport:
    def __init__(self, transport: Transport, directory: Path) -> None:
        self.transport: Transport = transport
        self.directory: Path = directory

    def _path(self, request: Request) -> Path:
        identity = f"v1:{request.url}:{request.json}"
        return self.directory / f"{hashlib.sha256(identity.encode()).hexdigest()}.json"

    def invalidate(self, request: Request) -> None:
        self._path(request).unlink(missing_ok=True)

    def fetch(self, request: Request) -> FetchOutcome:
        path = self._path(request)
        if path.exists():
            try:
                record = validate_json(
                    cast(object, json.loads(path.read_text(encoding="utf-8")))
                )
                if isinstance(record, dict) and record.get("url") == request.url:
                    payload = record.get("payload")
                    if isinstance(payload, (dict, str)):
                        return FetchOutcome(request, payload)
            except (ValueError, OSError):
                pass
        result = self.transport.fetch(request)
        if result.failure is None and result.payload is not None:
            write_json(path, {"url": request.url, "payload": result.payload})
        return result
