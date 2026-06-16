"""In-process rebase-transition handlers (the git branch of ``run_command``).

This is the in-process replacement for ``mediate.py``'s socket-and-exit-code
handlers. Each handler reuses the :class:`~forklift_harness.rebase_state.RebaseState`
engine verbatim (classification, continue-check, clean-empty handling, host
events, reset-conflict) but, instead of round-tripping a ``TransitionReport`` over
the control socket and returning a process exit code, it:

* records the transition into the shared :class:`~forklift_harness.agent_deps.RunReport`,
* mutates the shared :class:`~forklift_harness.agent_deps.AgentDeps` flags to drive
  the loop -- ``terminal`` when the rebase completes or aborts, ``transition_done``
  to end a conflict-mode session, and neither in rebase mode, and
* returns a human-readable string that becomes the ``run_command`` tool result the
  model reads.

The two lifetime modes are encoded here as control flow (design Decision 5): a
rebase-mode advance returns the next-conflict summary and leaves the run going; a
conflict-mode advance sets ``transition_done`` so the loop starts a fresh session.
"""

from __future__ import annotations

from typing import cast

import structlog

from .agent_deps import AgentDeps
from .rebase_state import (
    REASON_FLAG,
    RESOLUTION_NOTE_FLAG,
    RebaseState,
    classify_paused_rebase_command,
)

logger = cast("structlog.typing.FilteringBoundLogger", structlog.get_logger(__name__))

# Lifetime modes (FORKLIFT_AGENT_LIFETIME).
LIFETIME_REBASE = "rebase"
LIFETIME_CONFLICT = "conflict"

# Terminal exit codes, mirroring the legacy orchestrator finalizers.
TERMINAL_COMPLETED = 0
TERMINAL_STUCK = 0

UNSUPPORTED_GUIDANCE = (
    "git: unsupported paused rebase command.\n\n"
    "Forklift is mediating this paused rebase. Do not alter Git behavior or\n"
    "bypass the wrapper with config overrides, aliases, alternate Git paths, or\n"
    "unsupported Git commands.\n\n"
    "Resolve conflicts, stage the resolved files, then use one of:\n"
    f'  git rebase --continue {RESOLUTION_NOTE_FLAG} "<what changed & why>"\n'
    f'  git rebase --skip {RESOLUTION_NOTE_FLAG} "<why this commit is dropped>"\n'
    f'  git rebase --abort {REASON_FLAG} "<what blocked progress>"\n'
    "  git reset-conflict   (restore this conflict to its original state and start over)\n"
)


def _note_required(command: str, flag: str) -> str:
    return (
        f'git: {command} requires {flag} "<message>".\n\n'
        "Forklift records this note so the host can summarize how the rebase was\n"
        "resolved. Provide a non-empty message describing what changed and why,\n"
        f'then retry, e.g.:\n  {command} {flag} "<message>"\n'
    )


def _next_pause_summary(state: RebaseState) -> str:
    """Describe the now-current paused conflict for the agent (rebase-mode advance)."""

    progress = state.read_progress()
    identity = state.rebase_head_identity()
    if identity is None or progress is None:
        return "Resolved. Rebase advanced to the next step."
    sha, subject = identity
    files = ", ".join(progress.files) if progress.files else "(none reported)"
    return (
        f"Resolved. Rebase advanced to step {progress.step}/{progress.total}: "
        f"{sha} {subject}. Conflicted files: {files}. Resolve this conflict next."
    )


def _signal_advance(deps: AgentDeps) -> str:
    """Apply the lifetime branch after a transition that left the rebase paused."""

    if deps.config.agent_lifetime == LIFETIME_REBASE:
        # Same run continues to the next conflict.
        return _next_pause_summary(deps.state)
    # Conflict mode: end this session; the loop relaunches a fresh session.
    deps.transition_done = True
    return _next_pause_summary(deps.state)


def handle_continue(deps: AgentDeps, note: str | None) -> str:
    """Mediate ``git rebase --continue``: run the continue-check, then advance."""

    state = deps.state
    if not note:
        return _note_required("git rebase --continue", RESOLUTION_NOTE_FLAG)

    state.emit_phase("rebase", "stdout", "Intercepted git rebase --continue")
    identity = state.rebase_head_identity()
    sha, subject = identity if identity else ("", "")
    progress = state.read_progress()
    files = progress.files if progress else ()
    total = progress.total if progress else 0

    if state.is_clean_empty_stop():
        # Agent resolved this conflict into a mechanically empty commit; git would
        # refuse --continue, so the harness drops it, recording it as intended.
        _ = state.auto_skip_clean_empty_stop()
        deps.report.record_continue(sha, subject, note)
        logger.info(
            "rebase transition",
            action="continue",
            sha=sha,
            subject=subject,
            files=len(files),
            note=note,
        )
        return _finish_after_transition(deps)

    if state.has_continue_check():
        before = state.capture_status_snapshot()
        state.emit_phase("rebase", "stdout", "Running frozen rebase continue check")
        check = state.run_continue_check()
        after = state.capture_status_snapshot()
        if check.exit_code != 0:
            return _continue_failure(
                state, "Rebase continue check failed.", check.exit_code, after
            )
        if after != before:
            return _continue_failure(
                state, "Rebase continue check changed workspace state.", 0, after
            )
        state.emit_phase(
            "rebase",
            "stdout",
            "Rebase continue check passed with stable workspace state",
        )

    state.emit_phase("rebase", "stdout", "Invoking real git rebase --continue")
    result = state.run_real_git("rebase", "--continue")

    # `git rebase --continue` exits non-zero whenever it stops again -- including a
    # *successful* advance that pauses on the next conflict -- so the exit code does
    # not signal failure. Detect real progress by whether the rebase finished or
    # REBASE_HEAD moved to a different commit.
    if not state.rebase_in_progress():
        state.emit_post_transition_events(total)
    elif state.capture_status_snapshot() == "":
        state.emit_phase(
            "rebase",
            "stdout",
            "Auto-skipping mechanically empty commit after failed continue",
        )
        _ = state.auto_skip_clean_empty_stop()
    else:
        new_identity = state.rebase_head_identity()
        new_sha = new_identity[0] if new_identity else ""
        if not new_sha or new_sha == sha:
            # Same commit still applying: the continue was refused (e.g. unstaged
            # conflicts). Surface the failure so the agent fixes it in-session;
            # this is not a transition.
            message = (
                result.stderr.strip()
                or result.stdout.strip()
                or "git rebase --continue failed."
            )
            state.emit_phase(
                "rebase", "stderr", "git rebase --continue did not advance"
            )
            return (
                f"git rebase --continue failed; the rebase did not advance:\n{message}"
            )
        state.emit_post_transition_events(total)

    deps.report.record_continue(sha, subject, note)
    logger.info(
        "rebase transition",
        action="continue",
        sha=sha,
        subject=subject,
        files=len(files),
        note=note,
    )
    return _finish_after_transition(deps)


def handle_skip(deps: AgentDeps, note: str | None) -> str:
    """Mediate ``git rebase --skip``: drop the current commit, then advance."""

    state = deps.state
    if not note:
        return _note_required("git rebase --skip", RESOLUTION_NOTE_FLAG)

    state.emit_phase("rebase", "stdout", "Intercepted git rebase --skip")
    identity = state.rebase_head_identity()
    if identity is None:
        if state.is_clean_empty_stop():
            _ = state.auto_skip_clean_empty_stop()
            deps.report.record_skip("", "", note)
            logger.info(
                "rebase transition",
                action="skip",
                sha="",
                subject="",
                files=0,
                note=note,
            )
            return _finish_after_transition(deps)
        state.emit_phase(
            "rebase", "stderr", "Unable to determine REBASE_HEAD for git rebase --skip"
        )
        return "git rebase --skip failed: unable to determine the commit being skipped."

    sha, subject = identity
    progress = state.read_progress()
    files = progress.files if progress else ()
    total = progress.total if progress else 0
    _ = state.run_real_git("rebase", "--skip")
    state.emit_post_transition_events(total)
    deps.report.record_skip(sha, subject, note)
    logger.info(
        "rebase transition",
        action="skip",
        sha=sha,
        subject=subject,
        files=len(files),
        note=note,
    )
    return _finish_after_transition(deps)


def handle_abort(deps: AgentDeps, reason: str | None) -> str:
    """Mediate ``git rebase --abort``: report stuck and end the run."""

    state = deps.state
    if not reason:
        return _note_required("git rebase --abort", REASON_FLAG)

    state.emit_phase("rebase", "stdout", "Intercepted git rebase --abort")
    identity = state.rebase_head_identity()
    sha, subject = identity if identity else ("", "")
    _ = state.run_real_git("rebase", "--abort")
    deps.report.record_abort(sha, subject, reason)
    logger.info(
        "rebase transition",
        action="abort",
        sha=sha,
        subject=subject,
        files=0,
        note=reason,
    )
    deps.transition_done = True
    deps.terminal = TERMINAL_STUCK
    return "Rebase aborted; reported as stuck."


def handle_reset(deps: AgentDeps) -> str:
    """Mediate ``git reset-conflict``: restore the current step; not a transition."""

    state = deps.state
    state.emit_phase("rebase", "stdout", "Intercepted git reset-conflict")
    outcome = state.reset_current_conflict()
    if not outcome.ok:
        state.emit_phase(
            "rebase", "stderr", f"reset-conflict failed: {outcome.message}"
        )
        return f"git reset-conflict: {outcome.message}"
    state.emit_phase("rebase", "stdout", "Reset current conflict to its original state")
    return (
        "Current conflict reset to its original state. Re-resolve the conflicted\n"
        "files, stage them, then run "
        f'git rebase --continue {RESOLUTION_NOTE_FLAG} "<what changed & why>".'
    )


def passthrough(state: RebaseState, args: tuple[str, ...]) -> str:
    """Run an allowed read-only git command and return its combined output."""

    result = state.run_real_git(*args)
    return result.stdout + result.stderr


def unsupported(args: tuple[str, ...]) -> str:
    """Return the mediated-vocabulary guidance for an unsupported workspace git command."""

    rendered = "git " + " ".join(args)
    return f"{UNSUPPORTED_GUIDANCE}\nRejected command: {rendered}"


def mediate_workspace_git(deps: AgentDeps, args: tuple[str, ...]) -> str:
    """Classify and execute a workspace-repo git command during a paused rebase.

    This is the in-process equivalent of the old ``mediate.main()`` dispatch: the
    mediated rebase vocabulary is handled, read-only inspection passes through, and
    anything else returns the mediated-vocabulary guidance to the agent.
    """

    command = classify_paused_rebase_command(list(args))
    if command.action == "continue":
        return handle_continue(deps, command.resolution_note)
    if command.action == "skip":
        return handle_skip(deps, command.resolution_note)
    if command.action == "abort":
        return handle_abort(deps, command.reason)
    if command.action == "passthrough":
        return passthrough(deps.state, command.original_args)
    if command.action == "reset":
        return handle_reset(deps)
    return unsupported(args)


def _finish_after_transition(deps: AgentDeps) -> str:
    """Set terminal/lifetime flags after a recorded continue/skip transition."""

    if not deps.state.rebase_in_progress():
        deps.transition_done = True
        deps.terminal = TERMINAL_COMPLETED
        return "Rebase complete. All conflicts resolved."
    return _signal_advance(deps)


def _continue_failure(
    state: RebaseState, headline: str, exit_code: int, status_snapshot: str
) -> str:
    """Emit and return a continue-check failure; not a transition (agent retries)."""

    command_text = state.continue_check_command_text()
    message = (
        f"{headline}\n\n"
        f"Command:\n{command_text}\n\n"
        f"Exit code:\n{exit_code}\n\n"
        f"Workspace state after check:\n{status_snapshot}\n\n"
        "Resolve state, then retry rebase continue."
    )
    state.emit_phase("rebase", "stderr", "Blocking git rebase --continue")
    state.log_block("rebase", message)
    return message
