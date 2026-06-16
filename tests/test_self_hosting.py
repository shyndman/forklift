"""Self-hosting coverage: Forklift forking Forklift runs a suite that spawns real
git (and rebases) in temp repos while the workspace rebase is paused.

The target-repo discriminator must let nested git/rebases in *other* repositories
pass through unmediated (mutating verbs included) -- through both the in-process
``run_command`` toolset and the defensive backstop -- while still refusing
workspace rebase-state mutators outside the mediated vocabulary.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Coroutine
from typing import cast

from pydantic_ai import RunContext

from forklift_harness.agent_deps import AgentDeps, RunReport
from forklift_harness.backstop import decide
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


def _make_paused_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    _ = _git(workspace, "init", "-b", "main")
    _ = (workspace / "a.txt").write_text("base\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "base")
    base = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    _ = (workspace / "a.txt").write_text("upstream\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "upstream")
    upstream = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    _ = _git(workspace, "update-ref", "refs/remotes/upstream/main", upstream)
    _ = _git(workspace, "reset", "--hard", base)
    _ = (workspace / "a.txt").write_text("fork\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "fork")
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
    assert proc.returncode != 0  # paused on the conflict


def _make_clean_rebase_repo(repo: Path) -> None:
    """A temp repo whose `feature` rebases onto `main` with no conflict."""

    repo.mkdir(parents=True, exist_ok=True)
    _ = _git(repo, "init", "-b", "main")
    _ = (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _ = _git(repo, "add", "-A")
    _ = _git(repo, "commit", "-m", "c1")
    _ = _git(repo, "checkout", "-b", "feature")
    _ = (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _ = _git(repo, "add", "-A")
    _ = _git(repo, "commit", "-m", "feature work")
    _ = _git(repo, "checkout", "main")
    _ = (repo / "other.txt").write_text("other\n", encoding="utf-8")
    _ = _git(repo, "add", "-A")
    _ = _git(repo, "commit", "-m", "c2")
    _ = _git(repo, "checkout", "feature")


def _config(workspace: Path, harness_state: Path) -> HarnessConfig:
    harness_state.mkdir(parents=True, exist_ok=True)
    return HarnessConfig(
        workspace_dir=workspace,
        harness_state_dir=harness_state,
        real_git_bin=REAL_GIT,
        main_branch="main",
        upstream_ref="upstream/main",
        continue_check_file=harness_state / "rebase-continue-check.sh",
        agent_lifetime="conflict",
        conflict_index_snapshot=harness_state / "conflict-index",
    )


def _deps(workspace: Path, harness_state: Path) -> AgentDeps:
    config = _config(workspace, harness_state)
    return AgentDeps(state=RebaseState(config), config=config, report=RunReport())


def _ctx(deps: AgentDeps) -> RunContext[AgentDeps]:
    return cast(RunContext[AgentDeps], cast(object, SimpleNamespace(deps=deps)))


def _run(coro: Coroutine[object, object, str]) -> str:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# nested rebase in a temp repo passes through (toolset)
# --------------------------------------------------------------------------- #


def test_nested_temp_repo_rebase_passes_through_toolset(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_paused_workspace(workspace)
    nested = tmp_path / "nested"
    _make_clean_rebase_repo(nested)
    deps = _deps(workspace, tmp_path / "state")
    toolset = ForkliftGitToolset()

    # The agent's suite drives a nested rebase in another repo while the workspace
    # rebase is paused. It must run unmediated.
    _ = _run(toolset.run_command(_ctx(deps), f"git -C {nested} rebase main"))

    # The nested rebase completed: feature now sits on top of main's c2.
    nested_log = _git(nested, "log", "--oneline").stdout
    assert "feature work" in nested_log and "c2" in nested_log
    assert (nested / "other.txt").exists()  # main's commit is now in feature's history
    # The mediator did not treat it as a transition, and the workspace stays paused.
    assert deps.transition_done is False
    assert deps.report.resolutions == []
    assert deps.state.rebase_in_progress() is True


def test_nested_temp_repo_rebase_allowed_by_backstop(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_paused_workspace(workspace)
    nested = tmp_path / "nested"
    _make_clean_rebase_repo(nested)
    state = RebaseState(_config(workspace, tmp_path / "state"))

    assert decide(["rebase", "main"], state, cwd=nested, env={}) is True


# --------------------------------------------------------------------------- #
# workspace mutators outside the vocabulary are refused
# --------------------------------------------------------------------------- #


def test_workspace_reset_hard_refused_by_toolset(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_paused_workspace(workspace)
    deps = _deps(workspace, tmp_path / "state")
    toolset = ForkliftGitToolset()

    result = _run(toolset.run_command(_ctx(deps), "git reset --hard HEAD"))

    assert "unsupported paused rebase command" in result.lower()
    assert deps.transition_done is False
    assert deps.state.rebase_in_progress() is True


def test_workspace_reset_hard_refused_by_backstop(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_paused_workspace(workspace)
    state = RebaseState(_config(workspace, tmp_path / "state"))

    assert decide(["reset", "--hard", "HEAD"], state, cwd=workspace, env={}) is False
