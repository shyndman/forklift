from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

RUN_STATE_FILENAME = "run-state.json"
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "timed_out"})


class RunStateError(RuntimeError):
    """Raised when run-state metadata cannot be parsed or persisted safely."""


def utc_now_iso8601() -> str:
    """Return a timezone-aware UTC timestamp for lifecycle metadata fields."""

    return datetime.now(tz=timezone.utc).isoformat()


def run_state_path(run_dir: Path) -> Path:
    """Return the canonical run-state metadata path for a run directory."""

    return run_dir / RUN_STATE_FILENAME


def read_run_state(path: Path) -> dict[str, object]:
    """Load run-state metadata and enforce the top-level JSON object shape."""

    try:
        raw_payload = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - passthrough message is enough
        raise RunStateError(
            f"Unable to read run-state metadata at {path}: {exc}"
        ) from exc

    try:
        decoded_raw = cast(object, json.loads(raw_payload))
    except json.JSONDecodeError as exc:
        raise RunStateError(
            f"run-state metadata at {path} is not valid JSON: {exc.msg}"
        ) from exc

    if not isinstance(decoded_raw, dict):
        raise RunStateError(
            f"run-state metadata at {path} must be a JSON object, found {type(decoded_raw).__name__}."
        )

    return cast(dict[str, object], decoded_raw)


def write_run_state_atomic(path: Path, state: Mapping[str, object]) -> None:
    """Persist run-state JSON using fsync+replace so readers never see partial writes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(raw_temp_path)

    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(state, temp_file, indent=2, sort_keys=True)
            _ = temp_file.write("\n")
            temp_file.flush()
            _ = os.fsync(temp_file.fileno())

        os.replace(temp_path, path)

        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            _ = os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:  # pragma: no cover - platform/filesystem level errors
        raise RunStateError(
            f"Failed to atomically write run-state metadata at {path}: {exc}"
        ) from exc
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def initialize_run_state(run_dir: Path, run_id: str) -> dict[str, object]:
    """Create the initial run-state payload when a run directory is prepared."""

    payload: dict[str, object] = {
        "status": "starting",
        "run_id": run_id,
        "prepared_at": utc_now_iso8601(),
    }
    write_run_state_atomic(run_state_path(run_dir), payload)
    return payload


def update_run_state(path: Path, **updates: object) -> dict[str, object]:
    """Merge lifecycle fields into run-state metadata and persist atomically."""

    payload: dict[str, object]
    if path.exists():
        payload = read_run_state(path)
    else:
        payload = {}

    payload.update(updates)
    write_run_state_atomic(path, payload)
    return payload
