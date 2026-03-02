from __future__ import annotations

import json

from .clientlog_models import (
    FollowRenderState,
    FollowStepState,
    ParsedEvent,
    as_dict,
    as_str,
    looks_like_thought,
)

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
            is_pending = not any(item.event_type == "step_finish" for item in step_events)
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
        self,
        events: list[ParsedEvent],
        follow_state: FollowRenderState,
    ) -> str:
        """Render newly appended events while preserving per-step event order."""

        output_lines: list[str] = []
        for event in events:
            if not event.message_id:
                output_lines.append(self._render_inline_event(event))
                continue

            step_state = follow_state.steps.setdefault(event.message_id, FollowStepState())
            pending = event.event_type != "step_finish"
            status = "pending" if pending else "completed"
            title = f"Step {event.message_id} • {status} • live"
            output_lines.extend(self._box(title, self._render_step_event(event), pending=pending))

            step_state.emitted_events += 1
            if event.event_type == "step_finish":
                step_state.finished = True

        return "\n".join(output_lines).rstrip() + ("\n" if output_lines else "")

    def _render_inline_event(self, event: ParsedEvent) -> str:
        prefix = paint(format_relative(event.relative_ms), "subtle")
        if event.kind == "iso":
            text = event.text or event.raw_line
            return f"{prefix} {paint(text, 'foam')}"
        if event.kind == "raw":
            text = event.text or event.raw_line
            return f"{prefix} {paint(text, 'rose')}"
        return f"{prefix} {paint(event.raw_line, 'subtle')}"

    def _render_step_block(
        self,
        message_id: str,
        step_events: list[ParsedEvent],
        *,
        pending: bool,
    ) -> list[str]:
        status = "pending" if pending else "completed"
        title = f"Step {message_id} • {status}"
        body_lines: list[str] = []
        for event in step_events:
            body_lines.extend(self._render_step_event(event))
        return self._box(title, body_lines, pending=pending)

    def _render_step_event(self, event: ParsedEvent) -> list[str]:
        prefix = paint(format_relative(event.relative_ms), "subtle")
        if event.kind != "json" or event.payload is None:
            text = event.text or event.raw_line
            return [f"{prefix} {paint(text, 'rose')}"]

        event_type = event.event_type or "unknown"
        payload = event.payload
        part = as_dict(payload.get("part"))

        if event_type == "step_start":
            snapshot = as_str(part.get("snapshot")) if part else None
            summary = f"step_start part={event.part_id or 'n/a'}"
            if snapshot:
                summary += f" snapshot={snapshot}"
            return [f"{prefix} {paint(summary, 'foam')}"]

        if event_type == "text":
            text = event.text or ""
            style_name = "pine" if looks_like_thought(text) else "text"
            part_label = paint(f"part={event.part_id or 'n/a'}", "subtle")
            return [f"{prefix} {part_label} {paint(text, style_name, italic=True)}"]

        if event_type == "tool_use":
            return self._render_tool_event(prefix, part)

        if event_type == "step_finish":
            reason = as_str(part.get("reason")) if part else None
            tokens = as_dict(part.get("tokens")) if part else None
            summary = f"step_finish part={event.part_id or 'n/a'}"
            if reason:
                summary += f" reason={reason}"
            if tokens:
                summary += f" tokens={json.dumps(tokens, separators=(',', ':'))}"
            return [f"{prefix} {paint(summary, 'foam')}" ]

        fallback = json.dumps(payload, indent=2, ensure_ascii=False)
        fallback_title = f"event={event_type} part={event.part_id or 'n/a'} (raw fallback)"
        lines = [f"{prefix} {paint(fallback_title, 'gold')}" ]
        lines.extend(fallback.splitlines())
        return lines

    def _render_tool_event(
        self,
        prefix: str,
        part: dict[str, object] | None,
    ) -> list[str]:
        part_id = as_str(part.get("id")) if part else None
        tool_name = as_str(part.get("tool")) if part else None
        call_id = as_str(part.get("callID")) if part else None
        state = as_dict(part.get("state")) if part else None
        input_payload = as_dict(state.get("input")) if state else None
        metadata_payload = as_dict(state.get("metadata")) if state else None

        lines = [
            f"{prefix} {paint(f'tool {tool_name or "unknown"} part={part_id or "n/a"} call={call_id or "n/a"}', 'iris')}"
        ]

        description = as_str(input_payload.get("description")) if input_payload else None
        command = as_str(input_payload.get("command")) if input_payload else None
        if description:
            lines.append(f"{paint('description:', 'subtle')} {description}")
        if command:
            lines.append(f"{paint('command:', 'subtle')} {command}")

        output = as_str(state.get("output")) if state else None
        if not output and metadata_payload:
            output = as_str(metadata_payload.get("output"))
        if output:
            lines.append(paint("output:", "subtle"))
            lines.extend(output.splitlines() or [""])

        status = as_str(state.get("status")) if state else None
        if status:
            lines.append(f"{paint('status:', 'subtle')} {status}")
        return lines

    def _box(self, title: str, lines: list[str], *, pending: bool) -> list[str]:
        border_style = "gold" if pending else "iris"
        rendered = [paint(f"╭─ {title}", border_style)]
        for line in lines:
            rendered.append(f"{paint('│', border_style)} {line}")
        rendered.append(paint("╰─", border_style))
        return rendered


def paint(text: str, color_key: str, *, italic: bool = False) -> str:
    """Apply transcript palette color/style ANSI escapes to one line."""

    color = PALETTE[color_key]
    italic_prefix = "\x1b[3m" if italic else ""
    return f"{italic_prefix}{color}{text}{RESET}"


def format_relative(relative_ms: int) -> str:
    """Format elapsed milliseconds as `+MM:SS.mmm` for transcript output."""

    minutes, ms_remainder = divmod(max(relative_ms, 0), 60_000)
    seconds, milliseconds = divmod(ms_remainder, 1_000)
    return f"+{minutes:02}:{seconds:02}.{milliseconds:03}"
