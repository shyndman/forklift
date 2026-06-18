"""Workspace-independent QA-telemetry toolset for self-reported tool misbehaviour.

The conflict-resolution agent calls ``report_tool_issue`` whenever one of its
tools returns output that is unexpected, incorrect, or malformed, recording the
tool's name plus what it should have done instead. Each report is appended as one
JSON line to ``harness-state/tool-issues.jsonl`` so the host can inspect tool
quality after a run. Writing is deliberately best-effort: a write failure never
raises, so QA telemetry can never derail the rebase it is observing.
"""

from __future__ import annotations

import json
import time
from typing import cast

import structlog
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from .agent_deps import AgentDeps

# Newline-delimited JSON sink under the harness state dir, one report per line.
TOOL_ISSUES_FILENAME = "tool-issues.jsonl"

logger = cast("structlog.typing.FilteringBoundLogger", structlog.get_logger(__name__))


class DiagnosticsToolset(FunctionToolset[AgentDeps]):
    """A `FunctionToolset` exposing only the QA-telemetry ``report_tool_issue``."""

    def __init__(self) -> None:
        super().__init__()
        _ = self.add_function(self.report_tool_issue)

    def report_tool_issue(
        self, ctx: RunContext[AgentDeps], tool: str, description: str
    ) -> str:
        """Report that a tool returned unexpected, incorrect, or malformed output.

        Call this whenever any tool's output is not what you expected: name the
        offending ``tool`` and describe in ``description`` what it did wrong and
        what it should have done instead. This is fire-and-forget QA telemetry and
        does not affect the rebase outcome.
        """

        if not tool.strip():
            raise ModelRetry("Provide the name of the tool that misbehaved.")
        if not description.strip():
            raise ModelRetry(
                "Describe what the tool did wrong and what you expected instead."
            )

        logger.info("tool issue", tool=tool, description=description)

        record = {"ts": time.time(), "tool": tool, "description": description}
        path = ctx.deps.config.harness_state_dir / TOOL_ISSUES_FILENAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                _ = fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning("tool issue write failed", error=str(exc))

        return f"Recorded issue report for tool {tool!r}."
