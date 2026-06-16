"""Verifies the conflict-mode end-run driver (`forklift_harness.agent_deps`).

Proves the settled mechanism: a tool flips ``deps.transition_done``, the driver
breaks before the next model request (no extra round-trip -> ``usage.requests == 1``),
``run.usage`` is readable post-break, and a fresh session starts cleanly afterward.
Driven by a scripted FunctionModel so no provider is required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from forklift_harness.agent_deps import AgentDeps, RunReport, drive_until_transition
from forklift_harness.rebase_state import HarnessConfig, RebaseState


def _deps() -> AgentDeps:
    config = HarnessConfig(
        workspace_dir=Path("/workspace"),
        harness_state_dir=Path("/harness-state"),
        real_git_bin="/usr/bin/git",
        main_branch="main",
        upstream_ref="upstream/main",
        continue_check_file=Path("/harness-state/check.sh"),
        agent_lifetime="conflict",
    )
    return AgentDeps(state=RebaseState(config), config=config, report=RunReport())


def _transition_then_text(
    messages: list[ModelMessage], _info: AgentInfo
) -> ModelResponse:
    """Call the transition tool on the first turn, emit text on any later turn."""

    seen_tool_return = any(
        getattr(part, "part_kind", None) == "tool-return"
        for message in messages
        for part in message.parts
    )
    if not seen_tool_return:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="transition", args={"command": "x"})]
        )
    return ModelResponse(parts=[TextPart("done")])


def _build_agent() -> Agent[AgentDeps, str]:
    toolset = FunctionToolset[AgentDeps]()

    @toolset.tool
    def transition(ctx: RunContext[AgentDeps], command: str) -> str:
        _ = command
        # Conflict-mode advance: end this run from inside the tool.
        ctx.deps.transition_done = True
        return "transitioned"

    _ = transition  # silence unused-binding; registration is the decorator's side effect

    return Agent(
        FunctionModel(_transition_then_text),
        toolsets=[toolset],
        deps_type=AgentDeps,
        output_type=str,
    )


def test_driver_breaks_on_transition_without_extra_round_trip() -> None:
    agent = _build_agent()
    deps = _deps()

    usage = asyncio.run(drive_until_transition(agent, "resolve the conflict", deps))

    assert deps.transition_done is True
    # The model issued exactly one request: the transition ended the run before a
    # second ModelRequestNode could fire.
    assert usage.requests == 1
    assert usage.tool_calls == 1
    assert usage.total_tokens > 0


def test_fresh_session_starts_after_transition() -> None:
    agent = _build_agent()
    deps = _deps()

    first = asyncio.run(drive_until_transition(agent, "first conflict", deps))
    assert deps.transition_done is True

    # A fresh session (the loop's relaunch) runs cleanly and resets the flag.
    second = asyncio.run(drive_until_transition(agent, "second conflict", deps))
    assert deps.transition_done is True
    assert first.requests == 1
    assert second.requests == 1
