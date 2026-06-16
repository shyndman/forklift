"""Target-repo discriminator shared by the in-process git toolset and the backstop.

A git invocation is mediated only when it resolves to the workspace repository that
holds the live paused rebase. This module is the single source of truth for that
decision, imported by both the in-process ``run_command`` toolset and the binary
backstop shim so the two enforce identical rules.

The target repo is resolved **from argv only** -- the invocation's working
directory plus the repo-location global options ``-C``, ``--git-dir``, and
``--work-tree`` -- using real git's ``rev-parse --absolute-git-dir`` and comparing
the result against ``<workspace_dir>/.git``. The resolver never trusts the hidden
environment: any ``GIT_*`` variable supplied to the invocation fails the call
closed (``GitTarget.REJECTED``) instead of being resolved, mirroring the existing
config scrub in :meth:`forklift_harness.rebase_state.RebaseState._git_env`.

Callers decide *which* environment is inspected for ``GIT_*`` overrides: the
in-process toolset passes the command's env-prefix assignments (agent-authored,
auditable), while the backstop passes the process environment it was exec'd with.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Environment variables under this prefix redirect git's repository resolution
# (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...) or its behavior (GIT_CONFIG_*).
# Their presence fails an invocation closed rather than resolving it.
GIT_ENV_PREFIX = "GIT_"

# Repo-location global options that take a separate value argument. Only these
# affect which repository a git invocation targets; we replay them verbatim to
# real git when resolving the absolute git-dir.
_LOCATION_OPTIONS_WITH_VALUE = ("-C", "--git-dir", "--work-tree")

# Other global options that consume a following value token. We skip their values
# while walking the option prefix so a value is never mistaken for the subcommand
# or for a location option.
_OTHER_OPTIONS_WITH_VALUE = (
    "-c",
    "--namespace",
    "--super-prefix",
    "--config-env",
    "--attr-source",
)


class GitTarget(Enum):
    """Where a git invocation resolves, relative to the workspace repository."""

    WORKSPACE = "workspace"
    """Resolves to ``<workspace_dir>/.git`` -- the repo carrying the paused rebase."""

    OTHER = "other"
    """Resolves to a different repository, or to no repository at all."""

    REJECTED = "rejected"
    """A ``GIT_*`` environment override is present; the call fails closed."""


@dataclass(frozen=True)
class GitLocationOptions:
    """Repo-location global options extracted from a git invocation's argv."""

    dash_c: tuple[str, ...] = field(default_factory=tuple)
    git_dir: str | None = None
    work_tree: str | None = None


def has_git_env_override(env: Mapping[str, str]) -> bool:
    """Return whether any ``GIT_*`` variable is present in ``env`` (fail-closed signal)."""

    return any(key.startswith(GIT_ENV_PREFIX) for key in env)


def extract_location_options(argv: Sequence[str]) -> GitLocationOptions:
    """Extract ``-C``/``--git-dir``/``--work-tree`` from a git invocation's argv.

    ``argv`` is the token stream **after** the ``git`` program word (the same shape
    :func:`forklift_harness.rebase_state.classify_paused_rebase_command` consumes).
    Only the global-option prefix preceding the subcommand is inspected; options
    that take a value have their value skipped so it is never misread.
    """

    dash_c: list[str] = []
    git_dir: str | None = None
    work_tree: str | None = None

    index = 0
    while index < len(argv):
        arg = argv[index]
        if not arg.startswith("-"):
            # First non-option token is the subcommand; location options precede it.
            break
        if arg == "-C":
            index += 1
            if index < len(argv):
                dash_c.append(argv[index])
        elif arg == "--git-dir":
            index += 1
            if index < len(argv):
                git_dir = argv[index]
        elif arg.startswith("--git-dir="):
            git_dir = arg[len("--git-dir=") :]
        elif arg == "--work-tree":
            index += 1
            if index < len(argv):
                work_tree = argv[index]
        elif arg.startswith("--work-tree="):
            work_tree = arg[len("--work-tree=") :]
        elif arg in _OTHER_OPTIONS_WITH_VALUE:
            # Skip the value token (e.g. `-c key=value`) so it is not parsed.
            index += 1
        # Bare options (`--bare`, `-p`, `--config-env=...`, `--attr-source=...`,
        # and other `=`-joined forms) carry their value inline or none at all and
        # need no extra skip.
        index += 1

    return GitLocationOptions(
        dash_c=tuple(dash_c), git_dir=git_dir, work_tree=work_tree
    )


def _resolver_env(env: Mapping[str, str]) -> dict[str, str]:
    """Build a hermetic environment for the resolver's own ``rev-parse`` call.

    Strips every ``GIT_*`` variable from both the process environment and the
    supplied ``env`` so an ambient override can never steer the resolution that
    decides whether mediation applies.
    """

    resolved: dict[str, str] = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(GIT_ENV_PREFIX)
    }
    resolved.update(
        {key: value for key, value in env.items() if not key.startswith(GIT_ENV_PREFIX)}
    )
    return resolved


def resolve_git_target(
    argv: Sequence[str],
    *,
    cwd: Path | str,
    env: Mapping[str, str],
    workspace_git_dir: Path,
    real_git_bin: str,
) -> GitTarget:
    """Classify which repository a git invocation targets.

    ``argv`` is the git argument stream excluding the ``git`` program word; ``cwd``
    is the directory the command runs in; ``env`` is the environment whose
    ``GIT_*`` overrides should fail the call closed. Resolution replays the
    invocation's ``-C``/``--git-dir``/``--work-tree`` to real git and compares the
    absolute git-dir against ``workspace_git_dir``.
    """

    if has_git_env_override(env):
        return GitTarget.REJECTED

    options = extract_location_options(argv)
    command = [real_git_bin]
    for path in options.dash_c:
        command += ["-C", path]
    if options.git_dir is not None:
        command += ["--git-dir", options.git_dir]
    if options.work_tree is not None:
        command += ["--work-tree", options.work_tree]
    command += ["rev-parse", "--absolute-git-dir"]

    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=_resolver_env(env),
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, NotADirectoryError):
        # cwd or git-dir does not exist -> not the workspace repo.
        return GitTarget.OTHER

    if result.returncode != 0:
        # Not inside any git repository (or git refused the location options).
        return GitTarget.OTHER

    resolved_dir = Path(result.stdout.strip())
    workspace = Path(workspace_git_dir)
    try:
        same = resolved_dir.resolve() == workspace.resolve()
    except OSError:
        same = str(resolved_dir) == str(workspace)
    return GitTarget.WORKSPACE if same else GitTarget.OTHER
