from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast


@dataclass(frozen=True)
class ParsedEvent:
    """Normalized event shape used for rendering both snapshot and follow output."""

    kind: str
    raw_line: str
    relative_ms: int
    payload: dict[str, object] | None
    message_id: str | None
    part_id: str | None
    event_type: str | None
    text: str | None


@dataclass
class FollowStepState:
    """Tracks per-step live rendering progress so streamed events are emitted once."""

    emitted_events: int = 0
    finished: bool = False


@dataclass
class FollowRenderState:
    """Mutable rendering state carried across follow-mode polling iterations."""

    steps: dict[str, FollowStepState] = field(default_factory=dict)


def as_dict(value: object) -> dict[str, object] | None:
    """Return a typed dictionary when the input is an object payload map."""

    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


def as_str(value: object) -> str | None:
    """Return a string value when present in decoded payloads."""

    if isinstance(value, str):
        return value
    return None


def as_int(value: object) -> int | None:
    """Return integer timestamps from int/float payload fields, excluding bool."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def extract_text(
    payload: dict[str, object], part: dict[str, object] | None
) -> str | None:
    """Pick the best text field candidate from event payloads and part payloads."""

    candidates = [
        part.get("text") if part else None,
        payload.get("text"),
        payload.get("message"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            return candidate
    return None


def looks_like_thought(text: str) -> bool:
    """Detect assistant thought/reasoning prefixes for styled rendering."""

    lowered = text.lower()
    return lowered.startswith("thought:") or lowered.startswith("reasoning:")
