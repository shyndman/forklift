"""Parse and render end-of-run usage totals for Forklift container executions.

This module keeps post-run metric concerns isolated from orchestration flow in
``cli.py`` so callers can reliably compute and render one terminal summary block
for every run outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from collections.abc import Iterable
from typing import cast

from rich import box
from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text


USAGE_TABLE_WIDTH = 90
USAGE_TABLE_HEADER_STYLE = "bold cyan"
USAGE_TABLE_BORDER_STYLE = "dim"
USAGE_LABEL_STYLE = "dim"
USAGE_TOKEN_VALUE_STYLE = "bold white"
USAGE_COST_VALUE_STYLE = "bold green"


@dataclass(frozen=True)
class ToolCallTotal:
    """Represents per-tool call counts for nested post-run summary rows."""

    tool: str
    calls: int


@dataclass(frozen=True)
class UsageTotals:
    """Represents finalized usage totals shown in the run footer."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    total_tokens: int
    total_cost: float
    wall_clock_ms: int
    tool_calls: int
    tool_breakdown: tuple[ToolCallTotal, ...]


@dataclass(frozen=True)
class UsageSummary:
    """Encodes whether usage totals are available and why they may be missing."""

    available: bool
    totals: UsageTotals | None
    reason_unavailable: str | None

    @classmethod
    def from_totals(cls, totals: UsageTotals) -> UsageSummary:
        """Build an available summary for callers that computed totals."""

        return cls(available=True, totals=totals, reason_unavailable=None)

    @classmethod
    def unavailable(cls, reason: str) -> UsageSummary:
        """Build an unavailable summary with a user-facing reason."""

        return cls(available=False, totals=None, reason_unavailable=reason)


def parse_usage_summary(log_path: Path) -> UsageSummary:
    """Parse `opencode-client.log` and return usage totals for footer rendering."""

    try:
        with log_path.open("r", encoding="utf-8") as log_file:
            return _parse_usage_lines(log_file)
    except OSError as exc:
        return UsageSummary.unavailable(f"unable to read usage log: {exc}")


def render_usage_summary(
    outcome: str,
    summary: UsageSummary,
    *,
    console: Console | None = None,
) -> None:
    """Render the terminal-end run outcome and grand total metrics block."""

    active_console = console or Console()
    active_console.print(f"Run complete: {outcome}", markup=False)
    active_console.print()

    if not summary.available or summary.totals is None:
        reason = summary.reason_unavailable or "no usage events found"
        active_console.print("Grand total: unavailable", markup=False)
        active_console.print(f"Reason: {reason}", markup=False)
        return

    active_console.print(Align.center(_build_usage_table(summary.totals)))


def render_completion_report(
    workspace: Path,
    *,
    console: Console | None = None,
) -> Path | None:
    """Render terminal completion report markdown using STUCK-over-DONE precedence."""

    report_path = _select_report_path(workspace)
    if report_path is None:
        return None

    try:
        report_body = report_path.read_text(encoding="utf-8")
    except OSError:
        return None

    active_console = console or Console()
    active_console.print()
    active_console.print(Markdown(report_body))
    return report_path


def _parse_usage_lines(lines: Iterable[str]) -> UsageSummary:
    total_cost = 0.0
    final_snapshot: dict[str, object] | None = None
    saw_usage_payload = False
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None
    tool_call_counts: dict[str, int] = {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        payload = _parse_json_object(line)
        if payload is None:
            continue

        timestamp_ms = _timestamp_ms(payload.get("timestamp"))
        if timestamp_ms is not None:
            if first_timestamp_ms is None:
                first_timestamp_ms = timestamp_ms
            else:
                first_timestamp_ms = min(first_timestamp_ms, timestamp_ms)

            if last_timestamp_ms is None:
                last_timestamp_ms = timestamp_ms
            else:
                last_timestamp_ms = max(last_timestamp_ms, timestamp_ms)

        if payload.get("type") == "tool_use":
            part = _as_dict(payload.get("part"))
            tool_name = _tool_name(part)
            tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1

        if payload.get("type") != "step_finish":
            continue

        part = _as_dict(payload.get("part"))
        if part is None:
            continue

        cost = _as_number(part.get("cost"))
        if cost is not None:
            total_cost += cost
            saw_usage_payload = True

        tokens = _as_dict(part.get("tokens"))
        if tokens is None:
            continue

        if _as_number(tokens.get("total")) is None:
            continue

        final_snapshot = tokens
        saw_usage_payload = True

    if not saw_usage_payload or final_snapshot is None:
        return UsageSummary.unavailable("no usage events found")

    total_tool_calls = sum(tool_call_counts.values())
    tool_breakdown = tuple(
        ToolCallTotal(tool=tool_name, calls=calls)
        for tool_name, calls in sorted(tool_call_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    wall_clock_ms = _elapsed_wall_clock_ms(first_timestamp_ms, last_timestamp_ms)

    totals = UsageTotals(
        input_tokens=_token_value(final_snapshot, "input"),
        output_tokens=_token_value(final_snapshot, "output"),
        reasoning_tokens=_token_value(final_snapshot, "reasoning"),
        cache_read_tokens=_cache_read_tokens(final_snapshot),
        total_tokens=_token_value(final_snapshot, "total"),
        total_cost=total_cost,
        wall_clock_ms=wall_clock_ms,
        tool_calls=total_tool_calls,
        tool_breakdown=tool_breakdown,
    )
    return UsageSummary.from_totals(totals)


def _parse_json_object(line: str) -> dict[str, object] | None:
    try:
        decoded = cast(object, json.loads(line))
    except json.JSONDecodeError:
        return None

    if isinstance(decoded, dict):
        return cast(dict[str, object], decoded)
    return None


def _as_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    return None


def _timestamp_ms(value: object) -> int | None:
    number = _as_number(value)
    if number is None:
        return None
    return int(number)


def _tool_name(part: dict[str, object] | None) -> str:
    if part is None:
        return "unknown"

    raw_tool = part.get("tool")
    if isinstance(raw_tool, str):
        cleaned = raw_tool.strip()
        if cleaned:
            return cleaned
    return "unknown"


def _elapsed_wall_clock_ms(first_timestamp_ms: int | None, last_timestamp_ms: int | None) -> int:
    if first_timestamp_ms is None or last_timestamp_ms is None:
        return 0
    return max(last_timestamp_ms - first_timestamp_ms, 0)


def _token_value(tokens: dict[str, object], key: str) -> int:
    value = _as_number(tokens.get(key))
    if value is None:
        return 0
    return int(value)


def _cache_read_tokens(tokens: dict[str, object]) -> int:
    cache = _as_dict(tokens.get("cache"))
    if cache is None:
        return 0
    return _token_value(cache, "read")


def _build_usage_table(totals: UsageTotals) -> Table:
    table = Table(
        box=box.ROUNDED,
        width=USAGE_TABLE_WIDTH,
        header_style=USAGE_TABLE_HEADER_STYLE,
        border_style=USAGE_TABLE_BORDER_STYLE,
        pad_edge=True,
        title="Grand total",
        title_style="bold",
    )
    table.add_column("Metric", style=USAGE_LABEL_STYLE)
    table.add_column("Value", justify="right")
    table.add_row("Input", Text(_format_tokens(totals.input_tokens), style=USAGE_TOKEN_VALUE_STYLE))
    table.add_row("Output", Text(_format_tokens(totals.output_tokens), style=USAGE_TOKEN_VALUE_STYLE))
    table.add_row("Reasoning", Text(_format_tokens(totals.reasoning_tokens), style=USAGE_TOKEN_VALUE_STYLE))
    table.add_row("Cache read", Text(_format_tokens(totals.cache_read_tokens), style=USAGE_TOKEN_VALUE_STYLE))
    table.add_section()
    table.add_row("Total tokens", Text(_format_tokens(totals.total_tokens), style=USAGE_TOKEN_VALUE_STYLE))
    table.add_row("Wall clock", Text(_format_duration(totals.wall_clock_ms), style=USAGE_TOKEN_VALUE_STYLE))
    table.add_row("Tool calls", Text(_format_tokens(totals.tool_calls), style=USAGE_TOKEN_VALUE_STYLE))
    for tool_total in totals.tool_breakdown:
        table.add_row(
            f"    ↳ {tool_total.tool}",
            Text(_format_tokens(tool_total.calls), style=USAGE_LABEL_STYLE),
        )
    table.add_row("Total cost", Text(_format_cost(totals.total_cost), style=USAGE_COST_VALUE_STYLE))
    return table


def _format_tokens(value: int) -> str:
    return f"{value:,}"


def _format_cost(value: float) -> str:
    return f"${value:.4f}"


def _format_duration(value_ms: int) -> str:
    total_ms = max(value_ms, 0)
    minutes, ms_remainder = divmod(total_ms, 60_000)
    seconds, milliseconds = divmod(ms_remainder, 1_000)
    return f"{minutes:02}:{seconds:02}.{milliseconds:03}"


def _select_report_path(workspace: Path) -> Path | None:
    stuck_path = workspace / "STUCK.md"
    if stuck_path.exists():
        return stuck_path

    done_path = workspace / "DONE.md"
    if done_path.exists():
        return done_path

    return None
