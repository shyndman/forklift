from __future__ import annotations

import json
import re
from datetime import datetime
from typing import cast

from .clientlog_models import ParsedEvent, as_dict, as_int, as_str, extract_text

ISO_LINE_PATTERN = re.compile(r"^(?P<stamp>\d{4}-\d{2}-\d{2}T\S+)\s+(?P<text>.*)$")


class ClientLogParser:
    """Incrementally parses mixed harness ISO lines and JSON events from log chunks."""

    def __init__(self) -> None:
        self._buffer: str = ""
        self._start_ms: int | None = None
        self._last_ms: int | None = None

    def feed(self, chunk: str) -> list[ParsedEvent]:
        """Parse complete lines from a newly appended chunk."""

        self._buffer += chunk
        events: list[ParsedEvent] = []
        while "\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split("\n", 1)
            parsed = self._parse_line(raw_line.rstrip("\r"))
            if parsed is not None:
                events.append(parsed)
        return events

    def flush(self) -> list[ParsedEvent]:
        """Flush any trailing partial line (used by one-shot snapshot mode)."""

        if not self._buffer:
            return []
        pending = self._buffer.rstrip("\r")
        self._buffer = ""
        parsed = self._parse_line(pending)
        return [parsed] if parsed is not None else []

    def _parse_line(self, line: str) -> ParsedEvent | None:
        if not line:
            return None

        iso_match = ISO_LINE_PATTERN.match(line)
        if iso_match:
            event_ms = self._iso_to_ms(iso_match.group("stamp"))
            return ParsedEvent(
                kind="iso",
                raw_line=line,
                relative_ms=self._relative_ms(event_ms),
                payload=None,
                message_id=None,
                part_id=None,
                event_type="iso",
                text=iso_match.group("text"),
            )

        payload = self._parse_json(line)
        if payload is None:
            return ParsedEvent(
                kind="raw",
                raw_line=line,
                relative_ms=self._relative_ms(None),
                payload=None,
                message_id=None,
                part_id=None,
                event_type="raw",
                text=line,
            )

        part = as_dict(payload.get("part"))
        message_id = as_str(part.get("messageID")) if part else None
        part_id = as_str(part.get("id")) if part else None
        event_ms = as_int(payload.get("timestamp"))
        return ParsedEvent(
            kind="json",
            raw_line=line,
            relative_ms=self._relative_ms(event_ms),
            payload=payload,
            message_id=message_id,
            part_id=part_id,
            event_type=as_str(payload.get("type")),
            text=extract_text(payload, part),
        )

    def _relative_ms(self, event_ms: int | None) -> int:
        if event_ms is None:
            if self._start_ms is None:
                self._start_ms = 0
            reference = self._last_ms if self._last_ms is not None else self._start_ms
            return max(reference - self._start_ms, 0)

        if self._start_ms is None:
            self._start_ms = event_ms
        self._last_ms = event_ms
        return max(event_ms - self._start_ms, 0)

    def _iso_to_ms(self, raw_stamp: str) -> int | None:
        stamp = raw_stamp.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        return int(parsed.timestamp() * 1000)

    def _parse_json(self, line: str) -> dict[str, object] | None:
        try:
            decoded = cast(object, json.loads(line))
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return cast(dict[str, object], decoded)
        return None
