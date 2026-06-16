"""Tests for structlog telemetry emitted at the harness call sites.

These assert the event schema (design Decision) at the points it is emitted:
``"rebase transition"`` from the transition handlers, ``"tool exec"`` from the
git-mediating ``run_command``, and the agent-loop transcript records
(``"assistant"`` / ``"tool call"`` / ``"tool result"``) from
``drive_until_transition``. They use ``structlog.testing.capture_logs`` to
intercept events without depending on the rendered stdout format, and mirror the
paused-rebase fixture helpers from ``tests/test_toolset.py``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from structlog.testing import capture_logs

from forklift_harness.agent import build_agent
from forklift_harness.agent_deps import AgentDeps, RunReport, drive_until_transition
from forklift_harness.rebase_state import HarnessConfig, RebaseState
from forklift_harness.toolset import ForkliftGitToolset

REAL_GIT = shutil.which("git") or "/usr/bin/git"

_RETIRED_NOISE = "Unsupported paused rebase command shape"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
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


def _start_rebase(workspace: Path) -> None:
    proc = subprocess.run(
        [REAL_GIT, "rebase", "upstream/main"],
        cwd=str(workspace),
        env={
            **os.environ,
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0  # paused on a conflict


def _resolve_conflicts(workspace: Path) -> None:
    files = _git(workspace, "diff", "--name-only", "--diff-filter=U").stdout.split()
    for name in files:
        _ = (workspace / name).write_text("resolved\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")


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


def _deps(workspace: Path, harness_state: Path, *, agent_lifetime: str) -> AgentDeps:
    config = _config(workspace, harness_state, agent_lifetime=agent_lifetime)
    return AgentDeps(state=RebaseState(config), config=config, report=RunReport())


def _ctx(deps: AgentDeps) -> RunContext[AgentDeps]:
    # run_command only reads ctx.deps; a namespace is sufficient for unit testing.
    return cast(RunContext[AgentDeps], cast(object, SimpleNamespace(deps=deps)))


def _run(coro: Coroutine[object, object, str]) -> str:
    return asyncio.run(coro)


def _event_names(logs: object) -> list[str]:
    """Return the ``event`` string of each captured log entry."""

    assert isinstance(logs, list)
    entries = cast("list[dict[str, object]]", logs)
    names: list[str] = []
    for entry in entries:
        value = entry.get("event", "")
        if isinstance(value, str):
            names.append(value)
    return names


def test_continue_emits_rebase_transition_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    _resolve_conflicts(workspace)
    with capture_logs() as logs:
        _ = _run(
            toolset.run_command(
                _ctx(deps),
                'git rebase --continue --resolution-note "took upstream side"',
            )
        )

    transitions = [e for e in logs if e.get("event") == "rebase transition"]
    assert len(transitions) == 1
    event = transitions[0]
    assert event["action"] == "continue"
    assert event["note"] == "took upstream side"
    assert isinstance(event["files"], int)
    assert all(_RETIRED_NOISE not in name for name in _event_names(logs))


def test_run_command_emits_tool_exec_event(tmp_path: Path) -> None:
    # No rebase in progress, so run_command delegates to the shell.
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    with capture_logs() as logs:
        _ = _run(toolset.run_command(_ctx(deps), "echo hi"))

    tools = [e for e in logs if e.get("event") == "tool exec"]
    assert len(tools) == 1
    event = tools[0]
    assert event["tool"] == "run_command"
    assert event["command"] == "echo hi"
    assert event["ok"] is True
    assert isinstance(event["duration_ms"], int)
    assert all(_RETIRED_NOISE not in name for name in _event_names(logs))


def test_drive_until_transition_emits_transcript(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    # TestModel calls every available tool once, then returns the output text, so
    # the transcript carries a tool call, its result, and the final assistant text.
    agent = build_agent(model=TestModel(custom_output_text="done"), code_mode=False)

    with capture_logs() as logs:
        _ = asyncio.run(drive_until_transition(agent, "say hello", deps))

    calls = [e for e in logs if e.get("event") == "tool call"]
    run_command_calls = [e for e in calls if e.get("tool") == "run_command"]
    assert len(run_command_calls) == 1
    assert isinstance(run_command_calls[0]["args"], dict)
    assert "command" in run_command_calls[0]["args"]

    results = [e for e in logs if e.get("event") == "tool result"]
    run_command_results = [e for e in results if e.get("tool") == "run_command"]
    assert len(run_command_results) == 1
    assert isinstance(run_command_results[0]["content"], str)

    assistant = [e for e in logs if e.get("event") == "assistant"]
    assert any(e.get("text") == "done" for e in assistant)
    assert all(_RETIRED_NOISE not in name for name in _event_names(logs))
