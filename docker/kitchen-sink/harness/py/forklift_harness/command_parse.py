"""Find git invocations inside an agent-issued shell command string.

The in-process ``run_command`` tool receives a single command **string** that a
shell would run, not the clean ``argv`` the old binary shim got from the OS. To
mediate git we must locate every git invocation inside arbitrary shell. ``bashlex``
produces a bash AST; we walk it and collect each command node whose program word
is ``git`` -- across ``&&``/``||``/``;`` lists, pipelines, subshells, command
substitution, and env-prefixed commands -- together with its argv and any
env-prefix assignments.

``bashlex`` parses **syntax, not semantics**: it performs no expansion, so aliases,
``g=git; $g ...``, and ``eval`` defeat it. This module is therefore the mediation
front-end and common-case detector, **not** the security boundary -- the binary
backstop (see :mod:`forklift_harness.target_repo` consumers) catches whatever slips
through.

Per design: when a rebase is paused and the command cannot be parsed, the call is
failed closed as a :class:`pydantic_ai.exceptions.ModelRetry` so the agent rewrites
it in plainer shell. With no rebase in progress there is nothing to mediate and the
caller delegates the command unconditionally.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import override

import bashlex
import bashlex.ast
import bashlex.errors
from pydantic_ai.exceptions import ModelRetry


# Message returned to the model when bashlex cannot parse a command during a
# paused rebase. Kept short and actionable -- the agent should retype the command
# without the construct bashlex choked on (aliases, arithmetic, unbalanced quotes).
PARSE_RETRY_MESSAGE = (
    "Could not parse that shell command while a rebase is paused. "
    "Rewrite it as one or more plain commands (no aliases, eval, arithmetic, "
    "or unbalanced quotes) so git invocations are visible."
)


@dataclass(frozen=True)
class GitInvocation:
    """A single git command located inside a parsed shell command string."""

    program: str
    """The literal first word, e.g. ``git`` or ``/usr/bin/git``."""

    args: tuple[str, ...]
    """Tokens **after** the program word (the shape ``classify_paused_rebase_command`` consumes)."""

    env: dict[str, str] = field(default_factory=dict)
    """Env-prefix assignments applied to this command (``GIT_DIR=x git ...``)."""


class CommandParseError(Exception):
    """bashlex could not parse the command string into a shell AST."""


def _is_git_program(word: str) -> bool:
    """Return whether a program word invokes git (``git`` or any path ending in ``/git``)."""

    return os.path.basename(word) == "git"


def _parse_assignments(assignments: list[bashlex.ast.node]) -> dict[str, str]:
    """Turn ``NAME=VALUE`` assignment nodes into a mapping (value kept verbatim)."""

    env: dict[str, str] = {}
    for node in assignments:
        name, sep, value = node.word.partition("=")
        if sep and name:
            env[name] = value
    return env


class _GitInvocationCollector(bashlex.ast.nodevisitor):
    """AST visitor that records every git command node it reaches, recursively."""

    def __init__(self) -> None:
        self.invocations: list[GitInvocation] = []

    @override
    def visitcommand(self, n: bashlex.ast.node, parts: list[bashlex.ast.node]) -> None:
        words = [part for part in parts if part.kind == "word"]
        if words and _is_git_program(words[0].word):
            assignments = [part for part in parts if part.kind == "assignment"]
            self.invocations.append(
                GitInvocation(
                    program=words[0].word,
                    args=tuple(word.word for word in words[1:]),
                    env=_parse_assignments(assignments),
                )
            )
        # Return None so the visitor keeps recursing into nested nodes (command
        # substitutions, subshells) and finds git invocations buried inside them.
        return None


def parse_git_invocations(command: str) -> list[GitInvocation]:
    """Return every git invocation found in ``command``.

    Raises :class:`CommandParseError` when bashlex cannot parse the command
    (unbalanced quotes, arithmetic, or other unsupported constructs).
    """

    try:
        trees = bashlex.parse(command)
    except (bashlex.errors.ParsingError, NotImplementedError) as exc:
        raise CommandParseError(str(exc)) from exc

    collector = _GitInvocationCollector()
    for tree in trees:
        collector.visit(tree)
    return collector.invocations


def collect_git_invocations(
    command: str, *, rebase_paused: bool
) -> list[GitInvocation] | None:
    """Apply the paused-rebase parsing policy to ``command``.

    Returns ``None`` when no rebase is paused -- there is nothing to mediate, so
    the caller delegates the command unconditionally. When a rebase is paused,
    returns the git invocations found; on parse failure raises
    :class:`pydantic_ai.exceptions.ModelRetry` so the agent simplifies the command.
    """

    if not rebase_paused:
        return None
    try:
        return parse_git_invocations(command)
    except CommandParseError as exc:
        raise ModelRetry(PARSE_RETRY_MESSAGE) from exc
