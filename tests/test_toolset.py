"""Tests for the forklift git-mediation toolset (`forklift_harness.toolset`).

Exercises ``run_command`` directly over real paused-rebase fixtures: workspace
rebase verbs mediate and set the right ``AgentDeps`` flags, git targeting another
repo delegates (mutating verbs included), only ``run_command`` is exposed (no
background tools), and concurrent calls serialize rebase-state mutation behind
``deps.lock``.
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

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from forklift_harness.agent_deps import AgentDeps, RunReport
from forklift_harness.rebase_state import HarnessConfig, RebaseState
from forklift_harness.toolset import ForkliftGitToolset

REAL_GIT = shutil.which("git") or "/usr/bin/git"


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
    """Resolve every currently-conflicted file and stage it."""

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


def test_only_run_command_is_exposed() -> None:
    toolset = ForkliftGitToolset()
    assert set(toolset.tools) == {"run_command"}
    # No background-process lifecycle tools.
    for absent in ("start_command", "check_command", "stop_command"):
        assert absent not in toolset.tools


# --------------------------------------------------------------------------- #
# workspace mediation
# --------------------------------------------------------------------------- #


def test_continue_completes_single_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    _resolve_conflicts(workspace)
    result = _run(
        toolset.run_command(
            _ctx(deps), 'git rebase --continue --resolution-note "took upstream side"'
        )
    )

    assert "complete" in result.lower()
    assert deps.transition_done is True
    assert deps.terminal == 0
    assert len(deps.report.resolutions) == 1
    assert deps.report.resolutions[0]["note"] == "took upstream side"
    assert deps.state.rebase_in_progress() is False


def test_continue_conflict_mode_advances_and_ends_session(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 2)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    _resolve_conflicts(workspace)
    result = _run(
        toolset.run_command(
            _ctx(deps), 'git rebase --continue --resolution-note "fix one"'
        )
    )

    # Conflict mode: this session ends, but the rebase is still paused on conflict 2.
    assert deps.transition_done is True
    assert deps.terminal is None
    assert len(deps.report.resolutions) == 1
    assert deps.state.rebase_in_progress() is True
    assert "advanced" in result.lower()


def test_continue_rebase_mode_keeps_session_open(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 2)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="rebase")
    toolset = ForkliftGitToolset()

    _resolve_conflicts(workspace)
    _ = _run(
        toolset.run_command(
            _ctx(deps), 'git rebase --continue --resolution-note "fix one"'
        )
    )

    # Rebase mode: the same session continues (no transition_done flip).
    assert deps.transition_done is False
    assert deps.terminal is None
    assert len(deps.report.resolutions) == 1
    assert deps.state.rebase_in_progress() is True


def test_continue_check_failure_blocks_transition(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    # A non-empty, failing frozen continue-check must gate the transition.
    check = deps.config.continue_check_file
    check.parent.mkdir(parents=True, exist_ok=True)
    _ = check.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")

    _resolve_conflicts(workspace)
    result = _run(
        toolset.run_command(_ctx(deps), 'git rebase --continue --resolution-note "fix"')
    )

    assert "continue check failed" in result.lower()
    assert deps.transition_done is False
    assert deps.report.resolutions == []
    assert deps.state.rebase_in_progress() is True


def test_abort_reports_stuck_and_terminates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 2)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    _ = _run(
        toolset.run_command(_ctx(deps), 'git rebase --abort --reason "needs a human"')
    )

    assert deps.transition_done is True
    assert deps.terminal == 0
    assert deps.report.stuck is not None
    assert deps.report.stuck["reason"] == "needs a human"
    assert deps.state.rebase_in_progress() is False


def test_continue_without_note_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    result = _run(toolset.run_command(_ctx(deps), "git rebase --continue"))
    assert "requires" in result.lower()
    assert deps.transition_done is False
    assert deps.report.resolutions == []


def test_unsupported_workspace_git_returns_guidance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    result = _run(toolset.run_command(_ctx(deps), "git cherry-pick HEAD"))
    assert "unsupported paused rebase command" in result.lower()
    assert deps.transition_done is False


def test_passthrough_readonly_workspace_git(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    result = _run(toolset.run_command(_ctx(deps), "git status"))
    # Read-only inspection passes through and returns git output; no transition.
    assert "rebase" in result.lower() or "interactive" in result.lower() or result
    assert deps.transition_done is False
    assert deps.report.resolutions == []


def test_git_env_override_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    with pytest.raises(ModelRetry):
        _ = _run(
            toolset.run_command(
                _ctx(deps), 'GIT_DIR=/tmp/x git rebase --continue --resolution-note "x"'
            )
        )


# --------------------------------------------------------------------------- #
# delegation (target-repo discriminator)
# --------------------------------------------------------------------------- #


def test_other_repo_git_delegates_even_mutating(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    other = tmp_path / "other"
    _ = _git(_init_other(other), "rev-parse", "HEAD")

    # A mutating git command against another repo passes through unmediated.
    result = _run(
        toolset.run_command(
            _ctx(deps), f"git -C {other} commit --allow-empty -m delegated"
        )
    )
    log = _git(other, "log", "--oneline").stdout
    assert "delegated" in log
    assert deps.transition_done is False
    assert deps.report.resolutions == []
    assert deps.state.rebase_in_progress() is True
    _ = result


def test_non_git_command_delegates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()

    result = _run(toolset.run_command(_ctx(deps), "echo hello-from-shell"))
    assert "hello-from-shell" in result
    assert deps.transition_done is False


def _init_other(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _ = _git(path, "init", "-b", "main")
    _ = (path / "f.txt").write_text("x\n", encoding="utf-8")
    _ = _git(path, "add", "-A")
    _ = _git(path, "commit", "-m", "init")
    return path


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #


def test_mediation_serializes_behind_deps_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    deps = _deps(workspace, tmp_path / "state", agent_lifetime="conflict")
    toolset = ForkliftGitToolset()
    _resolve_conflicts(workspace)

    async def scenario() -> None:
        _ = await deps.lock.acquire()
        task = asyncio.ensure_future(
            toolset.run_command(
                _ctx(deps), 'git rebase --continue --resolution-note "x"'
            )
        )
        await asyncio.sleep(0.1)
        # The mediating call must block on the held lock and make no progress.
        assert not task.done()
        assert deps.report.resolutions == []
        deps.lock.release()
        _ = await task
        assert deps.report.resolutions != []

    asyncio.run(scenario())
