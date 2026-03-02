from __future__ import annotations

import json
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO, cast, override

from clypi import Command, Positional, arg

from .run_manager import DEFAULT_RUNS_ROOT
from .run_state import (
    RunStateError,
    TERMINAL_RUN_STATUSES,
    read_run_state,
    run_state_path,
)

ISO_LINE_PATTERN = re.compile(r"^(?P<stamp>\d{4}-\d{2}-\d{2}T\S+)\s+(?P<text>.*)$")

RESET = "\x1b[0m"
PALETTE = {
    "text": "\x1b[38;2;224;222;244m",
    "subtle": "\x1b[38;2;144;140;170m",
    "iris": "\x1b[38;2;196;167;231m",
    "foam": "\x1b[38;2;156;207;216m",
    "gold": "\x1b[38;2;246;193;119m",
    "rose": "\x1b[38;2;235;188;186m",
    "pine": "\x1b[38;2;49;116;143m",
}


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

        part = _as_dict(payload.get("part"))
        message_id = _as_str(part.get("messageID")) if part else None
        part_id = _as_str(part.get("id")) if part else None
        event_ms = _as_int(payload.get("timestamp"))
        return ParsedEvent(
            kind="json",
            raw_line=line,
            relative_ms=self._relative_ms(event_ms),
            payload=payload,
            message_id=message_id,
            part_id=part_id,
            event_type=_as_str(payload.get("type")),
            text=_extract_text(payload, part),
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


class TranscriptRenderer:
    """Renders parsed transcript events into Rosé Pine-inspired terminal output."""

    def render_snapshot(self, events: list[ParsedEvent]) -> str:
        """Render a one-shot transcript, grouping JSON events by messageID."""

        output_lines: list[str] = []
        grouped_steps: dict[str, list[ParsedEvent]] = {}
        step_order: list[str] = []

        for event in events:
            if not event.message_id:
                output_lines.append(self._render_inline_event(event))
                continue
            if event.message_id not in grouped_steps:
                grouped_steps[event.message_id] = []
                step_order.append(event.message_id)
            grouped_steps[event.message_id].append(event)

        for message_id in step_order:
            step_events = grouped_steps[message_id]
            is_pending = not any(
                item.event_type == "step_finish" for item in step_events
            )
            output_lines.extend(
                self._render_step_block(message_id, step_events, pending=is_pending)
            )

        return "\n".join(output_lines).rstrip() + ("\n" if output_lines else "")

    def initialize_follow_state(self, events: list[ParsedEvent]) -> FollowRenderState:
        """Capture existing step completion state before follow-mode streaming begins."""

        state = FollowRenderState()
        for event in events:
            if not event.message_id:
                continue
            step_state = state.steps.setdefault(event.message_id, FollowStepState())
            step_state.emitted_events += 1
            if event.event_type == "step_finish":
                step_state.finished = True
        return state

    def render_follow_events(
        self, events: list[ParsedEvent], follow_state: FollowRenderState
    ) -> str:
        """Render newly appended events while preserving per-step event order."""

        output_lines: list[str] = []
        for event in events:
            if not event.message_id:
                output_lines.append(self._render_inline_event(event))
                continue

            step_state = follow_state.steps.setdefault(
                event.message_id, FollowStepState()
            )
            pending = event.event_type != "step_finish"
            status = "pending" if pending else "completed"
            title = f"Step {event.message_id} • {status} • live"
            output_lines.extend(
                self._box(title, self._render_step_event(event), pending=pending)
            )

            step_state.emitted_events += 1
            if event.event_type == "step_finish":
                step_state.finished = True

        return "\n".join(output_lines).rstrip() + ("\n" if output_lines else "")

    def _render_inline_event(self, event: ParsedEvent) -> str:
        prefix = _paint(_format_relative(event.relative_ms), "subtle")
        if event.kind == "iso":
            text = event.text or event.raw_line
            return f"{prefix} {_paint(text, 'foam')}"
        if event.kind == "raw":
            text = event.text or event.raw_line
            return f"{prefix} {_paint(text, 'rose')}"
        return f"{prefix} {_paint(event.raw_line, 'subtle')}"

    def _render_step_block(
        self, message_id: str, step_events: list[ParsedEvent], pending: bool
    ) -> list[str]:
        status = "pending" if pending else "completed"
        title = f"Step {message_id} • {status}"
        body_lines: list[str] = []
        for event in step_events:
            body_lines.extend(self._render_step_event(event))
        return self._box(title, body_lines, pending=pending)

    def _render_step_event(self, event: ParsedEvent) -> list[str]:
        prefix = _paint(_format_relative(event.relative_ms), "subtle")
        if event.kind != "json" or event.payload is None:
            text = event.text or event.raw_line
            return [f"{prefix} {_paint(text, 'rose')}"]

        event_type = event.event_type or "unknown"
        payload = event.payload
        part = _as_dict(payload.get("part"))

        if event_type == "step_start":
            snapshot = _as_str(part.get("snapshot")) if part else None
            summary = f"step_start part={event.part_id or 'n/a'}"
            if snapshot:
                summary += f" snapshot={snapshot}"
            return [f"{prefix} {_paint(summary, 'foam')}"]

        if event_type == "text":
            text = event.text or ""
            style_name = "pine" if _looks_like_thought(text) else "text"
            part_label = _paint(f"part={event.part_id or 'n/a'}", "subtle")
            return [f"{prefix} {part_label} {_paint(text, style_name, italic=True)}"]

        if event_type == "tool_use":
            return self._render_tool_event(prefix, part)

        if event_type == "step_finish":
            reason = _as_str(part.get("reason")) if part else None
            tokens = _as_dict(part.get("tokens")) if part else None
            summary = f"step_finish part={event.part_id or 'n/a'}"
            if reason:
                summary += f" reason={reason}"
            if tokens:
                summary += f" tokens={json.dumps(tokens, separators=(',', ':'))}"
            return [f"{prefix} {_paint(summary, 'foam')}"]

        fallback = json.dumps(payload, indent=2, ensure_ascii=False)
        fallback_title = (
            f"event={event_type} part={event.part_id or 'n/a'} (raw fallback)"
        )
        lines = [f"{prefix} {_paint(fallback_title, 'gold')}"]
        lines.extend(fallback.splitlines())
        return lines

    def _render_tool_event(
        self, prefix: str, part: dict[str, object] | None
    ) -> list[str]:
        part_id = _as_str(part.get("id")) if part else None
        tool_name = _as_str(part.get("tool")) if part else None
        call_id = _as_str(part.get("callID")) if part else None
        state = _as_dict(part.get("state")) if part else None
        input_payload = _as_dict(state.get("input")) if state else None
        metadata_payload = _as_dict(state.get("metadata")) if state else None

        lines = [
            f"{prefix} {_paint(f'tool {tool_name or "unknown"} part={part_id or "n/a"} call={call_id or "n/a"}', 'iris')}"
        ]

        description = (
            _as_str(input_payload.get("description")) if input_payload else None
        )
        command = _as_str(input_payload.get("command")) if input_payload else None
        if description:
            lines.append(f"{_paint('description:', 'subtle')} {description}")
        if command:
            lines.append(f"{_paint('command:', 'subtle')} {command}")

        output = _as_str(state.get("output")) if state else None
        if not output and metadata_payload:
            output = _as_str(metadata_payload.get("output"))
        if output:
            lines.append(_paint("output:", "subtle"))
            lines.extend(output.splitlines() or [""])

        status = _as_str(state.get("status")) if state else None
        if status:
            lines.append(f"{_paint('status:', 'subtle')} {status}")
        return lines

    def _box(self, title: str, lines: list[str], pending: bool) -> list[str]:
        border_style = "gold" if pending else "iris"
        rendered = [_paint(f"╭─ {title}", border_style)]
        for line in lines:
            rendered.append(f"{_paint('│', border_style)} {line}")
        rendered.append(_paint("╰─", border_style))
        return rendered


class Clientlog(Command):
    """Render and optionally follow the OpenCode client transcript for a run."""

    run_id: Positional[str]
    follow: bool = arg(False, short="f", help="Continue streaming appended events")

    @override
    async def run(self) -> None:
        run_dir = self._resolve_run_dir(self.run_id)
        client_log_path = run_dir / "harness-state" / "opencode-client.log"
        run_state_file = run_state_path(run_dir)
        _ = self._load_required_run_state(run_state_file)

        if not client_log_path.exists():
            raise SystemExit(
                f"clientlog error: missing transcript file at {client_log_path}."
            )

        parser = ClientLogParser()
        renderer = TranscriptRenderer()

        with client_log_path.open("r", encoding="utf-8") as handle:
            initial_chunk = handle.read()
            history_events = parser.feed(initial_chunk)
            if not self.follow:
                history_events.extend(parser.flush())

            snapshot = renderer.render_snapshot(history_events)
            if snapshot:
                print(snapshot, end="")

            if self.follow:
                follow_state = renderer.initialize_follow_state(history_events)
                self._follow_stream(
                    handle,
                    parser,
                    renderer,
                    follow_state,
                    run_state_file,
                )

    def _resolve_run_dir(self, run_id: str) -> Path:
        run_dir = (DEFAULT_RUNS_ROOT / run_id).expanduser().resolve()
        if not run_dir.exists():
            raise SystemExit(
                f"clientlog error: run directory '{run_id}' not found at {run_dir}."
            )
        return run_dir

    def _load_required_run_state(self, state_path: Path) -> dict[str, object]:
        if not state_path.exists():
            raise SystemExit(
                f"clientlog error: required run-state metadata is missing at {state_path}."
            )
        try:
            return read_run_state(state_path)
        except RunStateError as exc:
            raise SystemExit(
                f"clientlog error: required run-state metadata at {state_path} is unreadable: {exc}"
            ) from exc

    def _follow_stream(
        self,
        handle: TextIO,
        parser: ClientLogParser,
        renderer: TranscriptRenderer,
        follow_state: FollowRenderState,
        run_state_file: Path,
    ) -> None:
        interrupted = False
        idle_polls_after_terminal = 0

        def _signal_handler(_signum: int, _frame: object | None) -> None:
            nonlocal interrupted
            interrupted = True

        previous_handlers = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        for sig in previous_handlers:
            _ = signal.signal(sig, _signal_handler)

        try:
            while not interrupted:
                chunk = handle.read()
                if chunk:
                    new_events = parser.feed(chunk)
                    rendered = renderer.render_follow_events(new_events, follow_state)
                    if rendered:
                        print(rendered, end="", flush=True)
                    idle_polls_after_terminal = 0
                    continue

                if self._is_terminal_run_state(run_state_file):
                    idle_polls_after_terminal += 1
                    if idle_polls_after_terminal >= 3:
                        print(
                            _paint(
                                "follow mode: run reached terminal status; exiting.",
                                "subtle",
                            ),
                            file=sys.stderr,
                        )
                        return
                else:
                    idle_polls_after_terminal = 0

                time.sleep(0.25)
        finally:
            for sig, previous in previous_handlers.items():
                _ = signal.signal(sig, previous)

        print(_paint("Interrupted, exiting follow mode.", "subtle"), file=sys.stderr)
        raise SystemExit(130)

    def _is_terminal_run_state(self, run_state_file: Path) -> bool:
        state = self._load_required_run_state(run_state_file)
        status = _as_str(state.get("status"))
        return bool(status and status in TERMINAL_RUN_STATUSES)


def _as_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _extract_text(
    payload: dict[str, object], part: dict[str, object] | None
) -> str | None:
    candidates = [
        part.get("text") if part else None,
        payload.get("text"),
        payload.get("message"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            return candidate
    return None


def _looks_like_thought(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith("thought:") or lowered.startswith("reasoning:")


def _paint(text: str, color_key: str, italic: bool = False) -> str:
    color = PALETTE[color_key]
    italic_prefix = "\x1b[3m" if italic else ""
    return f"{italic_prefix}{color}{text}{RESET}"


def _format_relative(relative_ms: int) -> str:
    minutes, ms_remainder = divmod(max(relative_ms, 0), 60_000)
    seconds, milliseconds = divmod(ms_remainder, 1_000)
    return f"+{minutes:02}:{seconds:02}.{milliseconds:03}"
