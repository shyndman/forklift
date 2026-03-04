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

# Compact output intentionally suppresses these protocol-level internals.
SUPPRESSED_PROTOCOL_FIELDS = (
    "messageID",
    "part.id",
    "callID",
    "snapshot",
    "token/cost payloads",
)

SUCCESS_TOOL_STATUSES = {"completed", "success", "succeeded", "ok"}


class TranscriptRenderer:
    """Render transcript events with a compact, operator-first layout."""

    def render_snapshot(self, events: list[ParsedEvent]) -> str:
        """Render a one-shot transcript in event order with compact formatting."""

        output_lines: list[str] = []
        for event in events:
            output_lines.extend(self._render_compact_event(event))
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
        """Render newly appended events using the same compact format as snapshots."""

        output_lines: list[str] = []
        for event in events:
            output_lines.extend(self._render_compact_event(event))
            if event.message_id:
                step_state = follow_state.steps.setdefault(event.message_id, FollowStepState())
                step_state.emitted_events += 1
                if event.event_type == "step_finish":
                    step_state.finished = True

        return "\n".join(output_lines).rstrip() + ("\n" if output_lines else "")

    def _render_compact_event(self, event: ParsedEvent) -> list[str]:
        """Route each parsed event through the compact formatter used by all modes."""

        if event.kind != "json" or event.payload is None:
            return [self._render_inline_event(event)]

        event_type = event.event_type or "unknown"
        if event_type == "tool_use":
            return self._render_compact_tool_event(event)

        if self._is_text_bearing_event(event_type, event.text):
            return self._render_compact_message_event(event)

        return self._render_compact_generic_event(event_type, event.relative_ms)

    def _render_inline_event(self, event: ParsedEvent) -> str:
        prefix = paint(format_relative(event.relative_ms), "subtle")
        if event.kind == "iso":
            text = event.text or event.raw_line
            return f"{prefix} {paint(f'INFO {text}', 'foam')}"
        if event.kind == "raw":
            text = event.text or event.raw_line
            return f"{prefix} {paint(f'RAW {text}', 'rose')}"
        return f"{prefix} {paint(f'EVENT {event.raw_line}', 'subtle')}"

    def _render_compact_message_event(self, event: ParsedEvent) -> list[str]:
        """Render text-bearing events as readable message blocks."""

        prefix = paint(format_relative(event.relative_ms), "subtle")
        text = event.text or ""
        is_thought = looks_like_thought(text)
        style_name = "pine" if is_thought else "text"
        return [
            f"{prefix} {paint('MESSAGE', 'foam')}",
            f"  {paint(text, style_name, italic=is_thought)}",
        ]

    def _render_compact_tool_event(self, event: ParsedEvent) -> list[str]:
        """Render tool events with only the operator-significant fields."""

        prefix = paint(format_relative(event.relative_ms), "subtle")
        part = as_dict(event.payload.get("part")) if event.payload else None
        tool_name = as_str(part.get("tool")) if part else None
        state = as_dict(part.get("state")) if part else None

        lines = [f"{prefix} {paint(f'TOOL {tool_name or "unknown"}', 'iris')}"]

        args_lines = self._extract_tool_args(state)
        if args_lines:
            lines.append(paint("  args:", "subtle"))
            lines.extend(f"    {line}" for line in args_lines)

        output = self._extract_tool_output(state)
        if output:
            lines.append(paint("  response:", "subtle"))
            lines.extend(f"    {line}" for line in (output.splitlines() or [""]))

        status = as_str(state.get("status")) if state else None
        if status and status.lower() not in SUCCESS_TOOL_STATUSES:
            lines.append(f"{paint('  status:', 'subtle')} {paint(status, 'rose')}")

        return lines

    def _render_compact_generic_event(self, event_type: str, relative_ms: int) -> list[str]:
        """Keep unknown JSON events visible without dumping raw payloads."""

        prefix = paint(format_relative(relative_ms), "subtle")
        return [f"{prefix} {paint(f'EVENT {event_type}', 'gold')}"]

    def _is_text_bearing_event(self, event_type: str, text: str | None) -> bool:
        """Detect events that should render as message text in compact mode."""

        if not text:
            return False
        return event_type not in {"tool_use", "step_start", "step_finish"}

    def _extract_tool_args(self, state: dict[str, object] | None) -> list[str]:
        """Extract concise tool argument lines, preferring description/command when present."""

        if state is None:
            return []
        input_payload = as_dict(state.get("input"))
        if input_payload is None:
            return []

        args: list[str] = []
        description = as_str(input_payload.get("description"))
        command = as_str(input_payload.get("command"))
        if description:
            args.append(f"description: {description}")
        if command:
            args.append(f"command: {command}")
        if args:
            return args

        return [json.dumps(input_payload, ensure_ascii=False, sort_keys=True)]

    def _extract_tool_output(self, state: dict[str, object] | None) -> str | None:
        """Extract tool response text from state output with metadata fallback."""

        if state is None:
            return None

        output = as_str(state.get("output"))
        if output:
            return output

        metadata_payload = as_dict(state.get("metadata"))
        if metadata_payload:
            return as_str(metadata_payload.get("output"))
        return None


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
