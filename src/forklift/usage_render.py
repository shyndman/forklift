"""Terminal rendering of LLM usage totals for the `forklift changelog` command.

These dataclasses and the Rich table renderer were factored out of the retired
``post_run_metrics`` module (which parsed the OpenCode client log). The rebase run
no longer renders a usage table -- it emits a structured summary event (see
``run_summary``) -- but the changelog command still presents its own per-run usage
totals through this shared shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rich import box
from rich.align import Align
from rich.console import Console
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
    total_cost: Decimal | float | None
    wall_clock_ms: int
    tool_calls: int
    conflicting_commits: int
    tool_breakdown: tuple[ToolCallTotal, ...]


@dataclass(frozen=True)
class UsageSummary:
    """Encodes whether usage totals are available and why they may be missing."""

    available: bool
    totals: UsageTotals | None
    reason_unavailable: str | None
    post_table_notice: str | None = None

    @classmethod
    def from_totals(
        cls,
        totals: UsageTotals,
        *,
        post_table_notice: str | None = None,
    ) -> UsageSummary:
        """Build an available summary for callers that computed totals."""

        return cls(
            available=True,
            totals=totals,
            reason_unavailable=None,
            post_table_notice=post_table_notice,
        )

    @classmethod
    def unavailable(cls, reason: str) -> UsageSummary:
        """Build an unavailable summary with a user-facing reason."""

        return cls(
            available=False,
            totals=None,
            reason_unavailable=reason,
            post_table_notice=None,
        )


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

    show_total_cost = not (
        summary.post_table_notice is not None and summary.totals.total_cost is None
    )
    active_console.print(
        Align.center(
            _build_usage_table(summary.totals, show_total_cost=show_total_cost)
        )
    )
    if summary.post_table_notice is not None:
        active_console.print(summary.post_table_notice, markup=False)


def _build_usage_table(totals: UsageTotals, *, show_total_cost: bool) -> Table:
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
    table.add_row(
        "Input",
        Text(_format_tokens(totals.input_tokens), style=USAGE_TOKEN_VALUE_STYLE),
    )
    table.add_row(
        "Output",
        Text(_format_tokens(totals.output_tokens), style=USAGE_TOKEN_VALUE_STYLE),
    )
    table.add_row(
        "Reasoning",
        Text(_format_tokens(totals.reasoning_tokens), style=USAGE_TOKEN_VALUE_STYLE),
    )
    table.add_row(
        "Cache read",
        Text(_format_tokens(totals.cache_read_tokens), style=USAGE_TOKEN_VALUE_STYLE),
    )
    table.add_section()
    table.add_row(
        "Total tokens",
        Text(_format_tokens(totals.total_tokens), style=USAGE_TOKEN_VALUE_STYLE),
    )
    table.add_row(
        "Wall clock",
        Text(_format_duration(totals.wall_clock_ms), style=USAGE_TOKEN_VALUE_STYLE),
    )
    table.add_row(
        "Tool calls",
        Text(_format_tokens(totals.tool_calls), style=USAGE_TOKEN_VALUE_STYLE),
    )
    for tool_total in totals.tool_breakdown:
        table.add_row(
            f"    ↳ {tool_total.tool}",
            Text(_format_tokens(tool_total.calls), style=USAGE_LABEL_STYLE),
        )
    table.add_row(
        "Conflicting commits",
        Text(_format_tokens(totals.conflicting_commits), style=USAGE_TOKEN_VALUE_STYLE),
    )
    if show_total_cost:
        table.add_row(
            "Total cost",
            Text(_format_cost(totals.total_cost), style=USAGE_COST_VALUE_STYLE),
        )
    return table


def _format_tokens(value: int) -> str:
    return f"{value:,}"


def _format_cost(value: Decimal | float | None) -> str:
    if value is None:
        return "n/a"
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    normalized = format(decimal_value.normalize(), "f")
    if "." not in normalized:
        normalized = f"{normalized}.0000"
    else:
        whole, fractional = normalized.split(".", 1)
        normalized = f"{whole}.{fractional.rstrip('0').ljust(4, '0')}"
    return f"${normalized}"


def _format_duration(value_ms: int) -> str:
    total_ms = max(value_ms, 0)
    minutes, ms_remainder = divmod(total_ms, 60_000)
    seconds, milliseconds = divmod(ms_remainder, 1_000)
    return f"{minutes:02}:{seconds:02}.{milliseconds:03}"
