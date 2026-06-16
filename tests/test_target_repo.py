"""Unit tests for the shared target-repo discriminator (`forklift_harness.target_repo`).

Covers the three classification outcomes the in-process toolset and the backstop
both rely on -- workspace vs. other repo vs. `GIT_*`-override rejection -- including
`-C`/`--git-dir`/`--work-tree` redirection that moves a command's target repo away
from or toward the workspace, plus the argv option extraction that underpins them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forklift_harness.target_repo import (
    GitLocationOptions,
    GitTarget,
    extract_location_options,
    has_git_env_override,
    resolve_git_target,
)

REAL_GIT = shutil.which("git") or "/usr/bin/git"


def _init_repo(path: Path) -> Path:
    """Initialize an empty git repo at ``path`` and return it."""

    path.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    _ = subprocess.run(
        [REAL_GIT, "init", "-q"],
        cwd=str(path),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return path


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(workspace, other)`` -- two independent git repositories."""

    workspace = _init_repo(tmp_path / "workspace")
    other = _init_repo(tmp_path / "other")
    return workspace, other


def _resolve(
    argv: list[str],
    *,
    cwd: Path,
    workspace: Path,
    env: dict[str, str] | None = None,
) -> GitTarget:
    return resolve_git_target(
        argv,
        cwd=cwd,
        env=env or {},
        workspace_git_dir=workspace / ".git",
        real_git_bin=REAL_GIT,
    )


# --------------------------------------------------------------------------- #
# resolve_git_target
# --------------------------------------------------------------------------- #


def test_command_in_workspace_is_workspace(repos: tuple[Path, Path]) -> None:
    workspace, _ = repos
    assert (
        _resolve(["status"], cwd=workspace, workspace=workspace) == GitTarget.WORKSPACE
    )


def test_command_in_workspace_subdir_is_workspace(repos: tuple[Path, Path]) -> None:
    workspace, _ = repos
    sub = workspace / "pkg" / "nested"
    sub.mkdir(parents=True)
    assert _resolve(["status"], cwd=sub, workspace=workspace) == GitTarget.WORKSPACE


def test_command_in_other_repo_is_other(repos: tuple[Path, Path]) -> None:
    workspace, other = repos
    assert _resolve(["status"], cwd=other, workspace=workspace) == GitTarget.OTHER


def test_non_repo_dir_is_other(repos: tuple[Path, Path], tmp_path: Path) -> None:
    workspace, _ = repos
    loose = tmp_path / "loose"
    loose.mkdir()
    assert _resolve(["status"], cwd=loose, workspace=workspace) == GitTarget.OTHER


def test_git_dir_redirect_away_from_workspace_is_other(
    repos: tuple[Path, Path],
) -> None:
    workspace, other = repos
    # Running inside the workspace but pointing --git-dir at the other repo.
    result = _resolve(
        ["--git-dir", str(other / ".git"), "status"],
        cwd=workspace,
        workspace=workspace,
    )
    assert result == GitTarget.OTHER


def test_dash_c_redirect_into_workspace_is_workspace(
    repos: tuple[Path, Path],
) -> None:
    workspace, other = repos
    # Running inside the other repo but -C into the workspace.
    result = _resolve(
        ["-C", str(workspace), "status"],
        cwd=other,
        workspace=workspace,
    )
    assert result == GitTarget.WORKSPACE


def test_git_dir_and_work_tree_redirect_into_workspace_is_workspace(
    repos: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    workspace, _ = repos
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _resolve(
        [
            "--git-dir",
            str(workspace / ".git"),
            "--work-tree",
            str(workspace),
            "status",
        ],
        cwd=elsewhere,
        workspace=workspace,
    )
    assert result == GitTarget.WORKSPACE


def test_git_dir_equals_form_redirect_is_other(repos: tuple[Path, Path]) -> None:
    workspace, other = repos
    result = _resolve(
        [f"--git-dir={other / '.git'}", "status"],
        cwd=workspace,
        workspace=workspace,
    )
    assert result == GitTarget.OTHER


# --------------------------------------------------------------------------- #
# GIT_* environment rejection (fail closed)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "git_env",
    [
        {"GIT_DIR": "/tmp/whatever/.git"},
        {"GIT_WORK_TREE": "/tmp/whatever"},
        {"GIT_INDEX_FILE": "/tmp/idx"},
        {"GIT_CONFIG_GLOBAL": "/tmp/cfg"},
    ],
)
def test_any_git_env_var_rejects(
    repos: tuple[Path, Path], git_env: dict[str, str]
) -> None:
    workspace, _ = repos
    # Even a command that would otherwise resolve to the workspace is rejected.
    result = _resolve(["status"], cwd=workspace, workspace=workspace, env=git_env)
    assert result == GitTarget.REJECTED


def test_non_git_env_var_does_not_reject(repos: tuple[Path, Path]) -> None:
    workspace, _ = repos
    result = _resolve(
        ["status"],
        cwd=workspace,
        workspace=workspace,
        env={"PATH": os.environ.get("PATH", ""), "FOO": "bar"},
    )
    assert result == GitTarget.WORKSPACE


def test_has_git_env_override_detects_prefix() -> None:
    assert has_git_env_override({"GIT_DIR": "x"}) is True
    assert has_git_env_override({"GITFOO": "x"}) is False
    assert has_git_env_override({"PATH": "x"}) is False
    assert has_git_env_override({}) is False


# --------------------------------------------------------------------------- #
# extract_location_options
# --------------------------------------------------------------------------- #


def test_extract_plain_command() -> None:
    assert extract_location_options(["status"]) == GitLocationOptions()


def test_extract_separate_value_options() -> None:
    opts = extract_location_options(
        ["-C", "sub", "--git-dir", "g", "--work-tree", "w", "status"]
    )
    assert opts == GitLocationOptions(dash_c=("sub",), git_dir="g", work_tree="w")


def test_extract_equals_form_options() -> None:
    opts = extract_location_options(["--git-dir=g", "--work-tree=w", "log"])
    assert opts == GitLocationOptions(git_dir="g", work_tree="w")


def test_extract_chains_multiple_dash_c() -> None:
    opts = extract_location_options(["-C", "a", "-C", "b", "status"])
    assert opts.dash_c == ("a", "b")


def test_extract_skips_dash_c_value_lookalike() -> None:
    # `-c key=value` must not be confused with a location option, and its value
    # must be skipped so it is never treated as the subcommand.
    opts = extract_location_options(["-c", "user.name=x", "--git-dir", "g", "status"])
    assert opts == GitLocationOptions(git_dir="g")


def test_extract_stops_at_subcommand() -> None:
    # A `--git-dir` appearing after the subcommand is a subcommand argument, not a
    # global location option, and is ignored.
    opts = extract_location_options(["status", "--git-dir", "g"])
    assert opts == GitLocationOptions()
