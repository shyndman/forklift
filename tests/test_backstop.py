"""Unit tests for the defensive git backstop (`forklift_harness.backstop`).

The backstop enforces the target-repo rule on grandchild git that bypassed the
in-process mediation toolset while the workspace rebase is paused. The 4.1a
contract is allow / allow / refuse for: nested workspace read-only, nested
temp-repo mutating git, and a workspace rebase-state mutator. We also cover the
``GIT_*``-redirected reconciliation (read-only flows, mutators refused) and the
``main`` entry point's refusal and exec paths.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forklift_harness.backstop import decide, main
from forklift_harness.rebase_state import HarnessConfig, RebaseState

REAL_GIT = shutil.which("git") or "/usr/bin/git"
HARNESS_PY_DIR = Path(__file__).resolve().parents[1] / "docker/kitchen-sink/harness/py"


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
    """Build a one-conflict repo and pause its rebase onto upstream/main."""

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


@pytest.fixture
def paused(tmp_path: Path) -> tuple[RebaseState, Path]:
    """Return ``(state, other_repo)``: a paused workspace + an independent repo."""

    workspace = tmp_path / "workspace"
    _make_paused_workspace(workspace)
    other = tmp_path / "other"
    other.mkdir()
    _ = _git(other, "init", "-b", "main")
    state = RebaseState(_config(workspace, tmp_path / "harness-state"))
    assert state.rebase_in_progress()
    return state, other


# --------------------------------------------------------------------------- #
# decide -- the 4.1a allow / allow / refuse contract
# --------------------------------------------------------------------------- #


def test_nested_workspace_read_only_is_allowed(
    paused: tuple[RebaseState, Path],
) -> None:
    state, _ = paused
    assert decide(["status"], state, cwd=state.config.workspace_dir, env={}) is True


def test_nested_temp_repo_mutating_git_is_allowed(
    paused: tuple[RebaseState, Path],
) -> None:
    state, other = paused
    allowed = decide(["commit", "--allow-empty", "-m", "x"], state, cwd=other, env={})
    assert allowed is True


def test_workspace_rebase_mutator_is_refused(paused: tuple[RebaseState, Path]) -> None:
    state, _ = paused
    assert (
        decide(["rebase", "--abort"], state, cwd=state.config.workspace_dir, env={})
        is False
    )


def test_workspace_continue_is_refused(paused: tuple[RebaseState, Path]) -> None:
    state, _ = paused
    refused = decide(
        ["rebase", "--continue"], state, cwd=state.config.workspace_dir, env={}
    )
    assert refused is False


# --------------------------------------------------------------------------- #
# decide -- GIT_*-redirected reconciliation
# --------------------------------------------------------------------------- #


def test_git_env_read_only_is_allowed(paused: tuple[RebaseState, Path]) -> None:
    # A GIT_*-carrying call (untrusted resolution) still flows if it is read-only;
    # this is how git's own read-only recursion gets through.
    state, other = paused
    git_dir = str(state.config.workspace_dir / ".git")
    assert decide(["status"], state, cwd=other, env={"GIT_DIR": git_dir}) is True


def test_git_env_workspace_mutator_is_refused(paused: tuple[RebaseState, Path]) -> None:
    # An agent disguising a workspace mutator via GIT_DIR is still refused.
    state, other = paused
    git_dir = str(state.config.workspace_dir / ".git")
    refused = decide(["rebase", "--abort"], state, cwd=other, env={"GIT_DIR": git_dir})
    assert refused is False


# --------------------------------------------------------------------------- #
# main -- refusal path and exec path
# --------------------------------------------------------------------------- #


def test_main_refuses_workspace_mutator(
    paused: tuple[RebaseState, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state, _ = paused
    monkeypatch.chdir(state.config.workspace_dir)
    monkeypatch.setenv("WORKSPACE_DIR", str(state.config.workspace_dir))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state.config.harness_state_dir))
    monkeypatch.setenv("REAL_GIT_BIN", REAL_GIT)
    monkeypatch.setenv("FORKLIFT_MAIN_BRANCH", "main")

    code = main(["rebase", "--abort"])

    assert code == 1
    assert "mediated" in capsys.readouterr().err


def test_main_execs_real_git_for_read_only(paused: tuple[RebaseState, Path]) -> None:
    # End-to-end through main(): the allow path execs real git, so we run it as a
    # subprocess and observe git's own output.
    state, _ = paused
    workspace = state.config.workspace_dir
    result = subprocess.run(
        [
            "python",
            "-m",
            "forklift_harness.backstop",
            "rev-parse",
            "--absolute-git-dir",
        ],
        cwd=str(workspace),
        env={
            **os.environ,
            "PYTHONPATH": str(HARNESS_PY_DIR),
            "WORKSPACE_DIR": str(workspace),
            "HARNESS_STATE_DIR": str(state.config.harness_state_dir),
            "REAL_GIT_BIN": REAL_GIT,
            "FORKLIFT_MAIN_BRANCH": "main",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str((workspace / ".git").resolve())
