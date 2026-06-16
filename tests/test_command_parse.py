"""Unit tests for bashlex-based git invocation discovery (`forklift_harness.command_parse`).

Covers compound/obfuscated command strings (lists, pipelines, subshells, command
substitution, env-prefix) and the paused-vs-not parse-failure policy: a paused
rebase fails closed with ModelRetry, while no rebase delegates unconditionally.
"""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelRetry

from forklift_harness.command_parse import (
    CommandParseError,
    GitInvocation,
    collect_git_invocations,
    parse_git_invocations,
)


def _args(invocations: list[GitInvocation]) -> list[tuple[str, ...]]:
    return [inv.args for inv in invocations]


# --------------------------------------------------------------------------- #
# parse_git_invocations -- structural discovery
# --------------------------------------------------------------------------- #


def test_simple_git_command() -> None:
    invs = parse_git_invocations("git status")
    assert _args(invs) == [("status",)]
    assert invs[0].program == "git"
    assert invs[0].env == {}


def test_non_git_command_yields_nothing() -> None:
    assert parse_git_invocations("ls -la /workspace") == []


def test_andor_list_collects_git_node() -> None:
    invs = parse_git_invocations("prep && git rebase --continue || true")
    assert _args(invs) == [("rebase", "--continue")]


def test_pipeline_with_command_substitution() -> None:
    invs = parse_git_invocations("echo $(git log --oneline) | cat")
    assert _args(invs) == [("log", "--oneline")]


def test_subshell_collects_multiple_git_nodes() -> None:
    invs = parse_git_invocations("git status; (cd /tmp/r && git init)")
    assert _args(invs) == [("status",), ("init",)]


def test_env_prefix_captured() -> None:
    invs = parse_git_invocations("GIT_DIR=/tmp/r/.git git -C sub status")
    assert len(invs) == 1
    assert invs[0].args == ("-C", "sub", "status")
    assert invs[0].env == {"GIT_DIR": "/tmp/r/.git"}


def test_multiple_env_assignments_captured() -> None:
    invs = parse_git_invocations("GIT_DIR=x A=1 git status")
    assert invs[0].env == {"GIT_DIR": "x", "A": "1"}


def test_absolute_path_git_is_detected() -> None:
    invs = parse_git_invocations("/usr/bin/git status")
    assert len(invs) == 1
    assert invs[0].program == "/usr/bin/git"
    assert invs[0].args == ("status",)


def test_non_git_path_lookalike_is_ignored() -> None:
    # A program whose basename is not exactly `git` must not be collected.
    assert parse_git_invocations("gitfoo status") == []
    assert parse_git_invocations("mygit status") == []


@pytest.mark.parametrize(
    "command",
    [
        'git status "',  # unbalanced quote -> MatchedPairError (a ParsingError)
        "git log $((1+",  # unbalanced arithmetic
    ],
)
def test_parse_failure_raises(command: str) -> None:
    with pytest.raises(CommandParseError):
        _ = parse_git_invocations(command)


# --------------------------------------------------------------------------- #
# collect_git_invocations -- paused-rebase policy
# --------------------------------------------------------------------------- #


def test_not_paused_delegates_unconditionally() -> None:
    # No rebase in progress: nothing to mediate, returns None (caller delegates).
    assert collect_git_invocations("git rebase --continue", rebase_paused=False) is None


def test_not_paused_ignores_parse_failure() -> None:
    # Not paused -> no parse attempt, so even garbage delegates without raising.
    assert collect_git_invocations('git status "', rebase_paused=False) is None


def test_paused_returns_invocations() -> None:
    invs = collect_git_invocations("git rebase --continue", rebase_paused=True)
    assert invs is not None
    assert _args(invs) == [("rebase", "--continue")]


def test_paused_parse_failure_raises_model_retry() -> None:
    with pytest.raises(ModelRetry):
        _ = collect_git_invocations('git status "', rebase_paused=True)
