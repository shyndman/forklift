"""Shared agent run state and the conflict-mode end-run driver.

With the agent loop running in-process, the orchestrator and the git-mediation
tool share one mutable object -- :class:`AgentDeps` -- carried as
``RunContext.deps`` through every tool call. The git-transition tool mutates it
(recording resolutions, flipping ``transition_done`` to end the current run, or
setting a ``terminal`` exit code), and the loop reads it after each
``agent.iter`` session to decide whether to relaunch a fresh session or finish.

The two rebase lifetime modes become control flow (design Decision 5):

* **rebase mode** -- a transition returns the next-conflict state as the tool's
  string result and leaves ``transition_done`` False, so the *same* ``agent.iter``
  run continues to the next conflict.
* **conflict mode** -- a transition sets ``transition_done`` True; the driver
  breaks out of node iteration *before the next model request* (no extra
  round-trip), the loop reads ``run.usage``, and starts a fresh ``agent.iter``
  for the now-current pause.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import cast

import structlog
from pydantic_ai import Agent
from pydantic_ai.messages import (
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage

from .rebase_state import HarnessConfig, RebaseState

logger = cast("structlog.typing.FilteringBoundLogger", structlog.get_logger(__name__))


@dataclass
class RunReport:
    """Accumulated rebase outcome, threaded across conflict-mode sessions.

    Mirrors the legacy ``orchestrate.RunReport`` but records transitions by their
    fields for the rebase report.
    """

    resolutions: list[dict[str, str]] = field(default_factory=list)
    skips: list[dict[str, str]] = field(default_factory=list)
    stuck: dict[str, str] | None = None

    def record_continue(self, sha: str, subject: str, note: str) -> None:
        self.resolutions.append({"sha": sha, "subject": subject, "note": note})

    def record_skip(self, sha: str, subject: str, note: str) -> None:
        self.skips.append({"sha": sha, "subject": subject, "note": note})

    def record_abort(self, sha: str, subject: str, reason: str) -> None:
        self.stuck = {"sha": sha, "subject": subject, "reason": reason}

    def to_payload(self, outcome: str) -> dict[str, object]:
        return {
            "outcome": outcome,
            "resolutions": self.resolutions,
            "skips": self.skips,
            "stuck": self.stuck,
        }


@dataclass
class AgentDeps:
    """Mutable run state shared between the orchestrator loop and the git tool."""

    state: RebaseState
    config: HarnessConfig
    report: RunReport = field(default_factory=RunReport)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    transition_done: bool = False
    """Set by the git-transition tool to end the current ``agent.iter`` run."""

    terminal: int | None = None
    """Terminal exit code (rebase completed or aborted); ends the loop, no relaunch."""

    session_usage: RunUsage = field(default_factory=RunUsage)
    """Live usage snapshot of the in-flight ``agent.iter`` session, refreshed per
    node so a cancelled (timed-out) session's partial usage is still recoverable."""


async def drive_until_transition(
    agent: Agent[AgentDeps, str], prompt: str, deps: AgentDeps
) -> RunUsage:
    """Run one ``agent.iter`` session, breaking when a transition ends the run.

    Drives the run node-by-node and breaks as soon as the git-transition tool sets
    ``deps.transition_done`` -- before the next ``ModelRequestNode`` issues its
    request, so a conflict-mode advance costs no extra model round-trip. Returns
    the session's aggregated :class:`~pydantic_ai.usage.RunUsage` (read while the
    run is still open) for pricing and accumulation across sessions.

    While iterating, each node is unpacked into transcript records: a completed
    assistant turn (``CallToolsNode``) yields ``"assistant"`` text and ``"tool
    call"`` records; the following request (``ModelRequestNode``) yields ``"tool
    result"`` / ``"tool retry"`` records. Fields are emitted untruncated.
    """

    deps.transition_done = False
    deps.session_usage = RunUsage()
    async with agent.iter(prompt, deps=deps) as run:
        async for node in run:
            if Agent.is_call_tools_node(node):
                for part in node.model_response.parts:
                    if isinstance(part, TextPart):
                        if part.content.strip():
                            logger.info("assistant", text=part.content)
                    elif isinstance(part, ToolCallPart):
                        logger.info(
                            "tool call",
                            tool=part.tool_name,
                            args=part.args_as_dict(),
                        )
            elif Agent.is_model_request_node(node):
                for part in node.request.parts:
                    if isinstance(part, ToolReturnPart):
                        logger.info(
                            "tool result",
                            tool=part.tool_name,
                            outcome=part.outcome,
                            content=part.model_response_str(),
                        )
                    elif isinstance(part, RetryPromptPart):
                        logger.info(
                            "tool retry",
                            tool=part.tool_name,
                            content=part.model_response(),
                        )
            deps.session_usage = cast(RunUsage, run.usage)
            if deps.transition_done:
                break
        return cast(RunUsage, run.usage)
