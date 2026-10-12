"""Atomic file writes at the application boundary."""

import json
import os
import tempfile
from pathlib import Path

from google.protobuf.json_format import MessageToJson

from ..schema.koris.v1.game_pb2 import Game


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    write_bytes(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode(
            "utf-8"
        ),
    )


def write_game(path: Path, game: Game, *, binary: bool = False) -> None:
    write_bytes(
        path,
        game.SerializeToString(deterministic=True)
        if binary
        else MessageToJson(game, preserving_proto_field_name=True).encode("utf-8"),
    )
