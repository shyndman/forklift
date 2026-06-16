"""Tests for the in-process orchestrator loop (`forklift_harness.orchestrate`).

Drives multi-conflict rebase fixtures through both lifetime modes with a scripted
FunctionModel "resolver bot" (no provider). Asserts the rebase completes, every
resolution note is recorded, conflict mode starts one session per conflict
(single-conflict-per-session) while rebase mode resolves all in one session, and a
non-resolving agent fails closed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from forklift_harness.agent import build_agent
from forklift_harness.agent_deps import AgentDeps
from forklift_harness.orchestrate import Orchestrator
from forklift_harness.rebase_state import HarnessConfig, RebaseState

REAL_GIT = shutil.which("git") or "/usr/bin/git"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    return subprocess.run(
        [REAL_GIT, *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _make_conflicting_repo(workspace: Path, n: int) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    _ = _git(workspace, "init", "-b", "main")
    for i in range(n):
        _ = (workspace / f"a{i}.txt").write_text("base\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "base")
    base = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    for i in range(n):
        _ = (workspace / f"a{i}.txt").write_text("upstream\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "upstream")
    upstream = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    _ = _git(workspace, "update-ref", "refs/remotes/upstream/main", upstream)
    _ = _git(workspace, "reset", "--hard", base)
    for i in range(n):
        _ = (workspace / f"a{i}.txt").write_text(f"main-{i}\n", encoding="utf-8")
        _ = _git(workspace, "add", "-A")
        _ = _git(workspace, "commit", "-m", f"main {i}")


def _config(
    workspace: Path, harness_state: Path, *, agent_lifetime: str
) -> HarnessConfig:
    harness_state.mkdir(parents=True, exist_ok=True)
    return HarnessConfig(
        workspace_dir=workspace,
        harness_state_dir=harness_state,
        real_git_bin=REAL_GIT,
        main_branch="main",
        upstream_ref="upstream/main",
        continue_check_file=harness_state / "rebase-continue-check.sh",
        agent_lifetime=agent_lifetime,
        conflict_index_snapshot=harness_state / "conflict-index",
    )


def _last_tool_return_text(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        for part in reversed(message.parts):
            if getattr(part, "part_kind", None) == "tool-return":
                return str(getattr(part, "content", ""))
    return ""


def _resolver_model(sessions: list[int]) -> FunctionModel:
    """A FunctionModel that resolves each conflict via a fixed read/stage/continue cycle."""

    def model_fn(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        responses = [m for m in messages if isinstance(m, ModelResponse)]
        if not responses:
            sessions.append(1)  # a fresh agent.iter session has begun
        returns = [
            part
            for message in messages
            for part in message.parts
            if getattr(part, "part_kind", None) == "tool-return"
        ]
        step = len(returns) % 4
        if step == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="run_command",
                        args={"command": "git diff --name-only --diff-filter=U"},
                    )
                ]
            )
        if step == 1:
            files = _last_tool_return_text(messages).split()
            target = files[0] if files else "a0.txt"
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="write_file",
                        args={"path": target, "content": "resolved\n"},
                    )
                ]
            )
        if step == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="run_command", args={"command": "git add -A"}
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_command",
                    args={
                        "command": 'git rebase --continue --resolution-note "auto-resolved"'
                    },
                )
            ]
        )

    return FunctionModel(model_fn)


def _orchestrator(
    tmp_path: Path, *, n: int, agent_lifetime: str, agent: Agent[AgentDeps, str]
) -> Orchestrator:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, n)
    config = _config(workspace, tmp_path / "state", agent_lifetime=agent_lifetime)
    state = RebaseState(config)
    return Orchestrator(config, state, agent, model_id="test")


def test_conflict_mode_one_session_per_conflict(tmp_path: Path) -> None:
    sessions: list[int] = []
    agent = build_agent(model=_resolver_model(sessions), code_mode=False)
    orch = _orchestrator(tmp_path, n=3, agent_lifetime="conflict", agent=agent)

    assert orch.run_initial_rebase() == "paused"
    exit_code = asyncio.run(orch.run_agent_loop())

    assert exit_code == 0
    assert orch.state.rebase_in_progress() is False
    assert len(orch.report.resolutions) == 3
    assert all(r["note"] == "auto-resolved" for r in orch.report.resolutions)
    # Conflict mode: a fresh session per conflict.
    assert len(sessions) == 3


def test_rebase_mode_single_session(tmp_path: Path) -> None:
    sessions: list[int] = []
    agent = build_agent(model=_resolver_model(sessions), code_mode=False)
    orch = _orchestrator(tmp_path, n=3, agent_lifetime="rebase", agent=agent)

    assert orch.run_initial_rebase() == "paused"
    exit_code = asyncio.run(orch.run_agent_loop())

    assert exit_code == 0
    assert orch.state.rebase_in_progress() is False
    assert len(orch.report.resolutions) == 3
    # Rebase mode: one session resolves every conflict.
    assert len(sessions) == 1


def test_usage_file_written(tmp_path: Path) -> None:
    sessions: list[int] = []
    agent = build_agent(model=_resolver_model(sessions), code_mode=False)
    orch = _orchestrator(tmp_path, n=1, agent_lifetime="conflict", agent=agent)
    assert orch.run_initial_rebase() == "paused"
    _ = asyncio.run(orch.run_agent_loop())

    usage_path = orch.config.harness_state_dir / "usage.json"
    assert usage_path.is_file()
    payload = cast("dict[str, object]", json.loads(usage_path.read_text()))
    assert payload["model"] == "test"
    assert cast(int, payload["requests"]) >= 1


def _giveup_model() -> FunctionModel:
    def model_fn(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        # Emit text immediately without resolving anything -> session ends, rebase
        # still paused.
        return ModelResponse(parts=[TextPart("I give up")])

    return FunctionModel(model_fn)


def test_agent_giveup_fails_closed(tmp_path: Path) -> None:
    agent = build_agent(model=_giveup_model(), code_mode=False)
    orch = _orchestrator(tmp_path, n=1, agent_lifetime="conflict", agent=agent)
    assert orch.run_initial_rebase() == "paused"
    exit_code = asyncio.run(orch.run_agent_loop())

    assert exit_code == 1
    assert orch.report.resolutions == []


def test_timeout_populates_stuck_block(tmp_path: Path) -> None:
    """A timed-out run writes a self-consistent stuck report (issue #5)."""

    sessions: list[int] = []
    agent = build_agent(model=_resolver_model(sessions), code_mode=False)
    orch = _orchestrator(tmp_path, n=1, agent_lifetime="conflict", agent=agent)
    assert orch.run_initial_rebase() == "paused"

    # Force the immediate-timeout guard (remaining <= 0) before any agent runs.
    orch.agent_timeout = 0
    exit_code = asyncio.run(orch.run_agent_loop())
    assert exit_code == 2

    payload = cast(
        "dict[str, object]",
        json.loads(
            (orch.config.harness_state_dir / "rebase-report.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert payload["outcome"] == "stuck"
    assert payload["stuck"] is not None
    stuck = cast("dict[str, object]", payload["stuck"])
    assert "timed out" in cast(str, stuck["reason"])
    assert isinstance(stuck["sha"], str)
    assert stuck["sha"]


def _timeout_model() -> FunctionModel:
    """First request issues an allowed paused command; the second blocks forever."""

    async def model_fn(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        responses = [m for m in messages if isinstance(m, ModelResponse)]
        if not responses:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="run_command", args={"command": "git status"}
                    )
                ]
            )
        await asyncio.sleep(10)
        return ModelResponse(parts=[TextPart(content="unreachable")])

    return FunctionModel(model_fn)


def test_timeout_preserves_partial_usage(tmp_path: Path) -> None:
    """The cut session's completed-request usage survives the TimeoutError (issue #5)."""

    agent = build_agent(model=_timeout_model(), code_mode=False)
    orch = _orchestrator(tmp_path, n=1, agent_lifetime="conflict", agent=agent)
    assert orch.run_initial_rebase() == "paused"

    orch.agent_timeout = 1
    exit_code = asyncio.run(orch.run_agent_loop())
    assert exit_code == 2

    assert orch.total_usage.requests >= 1
    payload = cast(
        "dict[str, object]",
        json.loads(
            (orch.config.harness_state_dir / "usage.json").read_text(encoding="utf-8")
        ),
    )
    assert cast(int, payload["requests"]) >= 1
