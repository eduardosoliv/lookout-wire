"""Atomic, durable JSON documents for state that must survive crashes and restarts."""

import fcntl
import json
import os
from pathlib import Path
from typing import Any


class StateFileError(Exception):
    pass


def temp_path(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = temp_path(path)
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.flush()
        _sync(handle.fileno())
    os.replace(temp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    temp_path(path).unlink(missing_ok=True)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise StateFileError(f"State file {path} is corrupt: {error}") from None


def _sync(fd: int) -> None:
    # macOS only flushes to the disk itself with F_FULLFSYNC; os.fsync stops at the drive cache.
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        return
    os.fsync(fd)
