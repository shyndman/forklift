"""Defensive git backstop for grandchild git that bypasses in-process mediation.

The agent only ever acts through the in-process ``run_command`` toolset, where
workspace-repo git is mediated. But processes the agent *spawns* -- the fork's
continue-check, test suites, build steps -- and git's own recursion (hooks, merge
drivers) call ``git`` via ``PATH``, which resolves to the thin shim
``includes/bin/git``. During a paused rebase that shim execs this module.

The backstop enforces the same target-repo rule as the toolset, using its own
*real* cwd/argv/env (the soundness boundary that covers bashlex's semantic blind
spot -- whatever obfuscation produced the exec, here we see the real command):

* git that does not target the workspace repo -> ``exec`` real git (mutating verbs
  included; test temp repos and tooling git-dirs are none of our business);
* workspace-repo read-only verbs (``ALLOWED_PAUSED_COMMANDS``) -> ``exec`` real git
  (the continue-check and git's own read-only recursion);
* workspace-repo rebase-state mutators that did not come through the in-process
  path -> refuse (nonzero + message).

``GIT_*`` reconciliation: a redirecting ``GIT_*`` variable makes argv-based
resolution untrustworthy, so :func:`resolve_git_target` fails it closed
(``REJECTED``). Rather than refusing every such call -- which would break git's
own recursion, since git always sets ``GIT_*`` for its children -- we fall back to
the read-only allowlist: a ``GIT_*``-carrying call passes only if it is read-only.
An agent trying to disguise a workspace mutator via ``GIT_DIR`` is still refused;
git's read-only recursion still flows. Mutating git recursion under ``GIT_*`` is
the one documented residual, validated against a real multi-conflict rebase.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .rebase_state import HarnessConfig, RebaseState, classify_paused_rebase_command
from .target_repo import GitTarget, resolve_git_target

# Action returned by classify_paused_rebase_command for read-only inspection
# commands (ALLOWED_PAUSED_COMMANDS) that are safe to pass through during a pause.
_PASSTHROUGH_ACTION = "passthrough"

# Refusal shown to whoever bypassed the in-process mediation path.
_REFUSAL_MESSAGE = (
    "git is mediated during a paused rebase; resolve the conflict through the "
    "harness (the agent's run_command tool), not by invoking git directly."
)


def _exec_real_git(real_git_bin: str, argv: list[str]) -> None:
    """Replace this process with the real git binary, preserving argv and env."""

    os.execv(real_git_bin, [real_git_bin, *argv])


def decide(
    argv: list[str], state: RebaseState, *, cwd: Path, env: dict[str, str]
) -> bool:
    """Return whether ``argv`` is allowed to reach real git during a paused rebase.

    ``True`` -> allow (the caller should exec real git); ``False`` -> refuse.
    Pure policy, separated from process replacement so it is unit-testable.
    """

    workspace_git_dir = state.config.workspace_dir / ".git"
    target = resolve_git_target(
        argv,
        cwd=cwd,
        env=env,
        workspace_git_dir=workspace_git_dir,
        real_git_bin=state.config.real_git_bin,
    )

    # Any other repository (temp repos, tooling git-dirs) is none of our business,
    # mutating verbs included.
    if target is GitTarget.OTHER:
        return True

    # WORKSPACE (trusted resolution) or REJECTED (GIT_*-redirected, untrusted):
    # allow only read-only inspection; refuse rebase-state mutators that bypassed
    # the in-process path.
    return classify_paused_rebase_command(argv).action == _PASSTHROUGH_ACTION


def main(argv: list[str] | None = None) -> int:
    """Backstop entry point: enforce the target-repo rule, then exec or refuse.

    ``argv`` is the git argument stream excluding the ``git`` program word
    (defaults to ``sys.argv[1:]``). Returns an exit code only on refusal or when
    real git cannot be located; on allow it execs real git and never returns.
    """

    args = list(sys.argv[1:] if argv is None else argv)
    config = HarnessConfig.from_env()
    state = RebaseState(config)

    # No paused rebase means nothing to protect; the shim already gates on this,
    # but stay correct if invoked directly.
    if not state.rebase_in_progress():
        _exec_real_git(config.real_git_bin, args)
        return 0  # unreachable when execv succeeds

    allowed = decide(args, state, cwd=Path.cwd(), env=dict(os.environ))
    if allowed:
        _exec_real_git(config.real_git_bin, args)
        return 0  # unreachable when execv succeeds

    print(_REFUSAL_MESSAGE, file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
