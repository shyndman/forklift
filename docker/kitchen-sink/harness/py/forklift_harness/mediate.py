"""Git-shim entrypoint invoked for every git call during a paused rebase.

The thin bash shim (`includes/bin/git`) detects an in-progress rebase and execs
`python3 -m forklift_harness.mediate <git args...>`. This module classifies the
command, enforces the mediated `--resolution-note`/`--reason` vocabulary, performs
the real rebase transition, emits host events, then reports the transition over
the intra-container control socket and blocks for the orchestrator's directive.

A continue-check failure returns control to the agent in-session (no socket
contact). Only a successful advance reports + blocks, letting the orchestrator
choose reply-and-continue (rebase mode) or kill-and-relaunch (conflict mode).

`git reset-conflict` is a non-transition recovery verb: it restores the current
paused step to git's original conflicted state and returns control to the agent
in-session, never contacting the control socket.
"""

from __future__ import annotations

import os
import sys

from .control import TransitionReport, send_report_and_wait
from .rebase_state import (
    REASON_FLAG,
    RESOLUTION_NOTE_FLAG,
    HarnessConfig,
    RebaseState,
    classify_paused_rebase_command,
)

# Upper bound on how long the mediator waits for the orchestrator directive
# before failing closed. The container watchdog is the ultimate backstop.
DEFAULT_RECV_TIMEOUT_SECONDS = 600.0


def _recv_timeout() -> float:
    raw = os.environ.get("FORKLIFT_CONTROL_RECV_TIMEOUT")
    if not raw:
        return DEFAULT_RECV_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_RECV_TIMEOUT_SECONDS


def _fail_unsupported(state: RebaseState, args: list[str]) -> int:
    rendered = "git " + " ".join(args)
    state.emit_phase(
        "rebase", "stderr", f"Unsupported paused rebase command shape: {rendered}"
    )
    print(
        "git: unsupported paused rebase command.\n\n"
        + "Forklift is mediating this paused rebase. Do not alter Git behavior or\n"
        + "bypass the wrapper with config overrides, aliases, alternate Git paths, or\n"
        + "unsupported Git commands.\n\n"
        + "Resolve conflicts, stage the resolved files, then use one of:\n"
        + f'  git rebase --continue {RESOLUTION_NOTE_FLAG} "<what changed & why>"\n'
        + f'  git rebase --skip {RESOLUTION_NOTE_FLAG} "<why this commit is dropped>"\n'
        + f'  git rebase --abort {REASON_FLAG} "<what blocked progress>"\n'
        + "  git reset-conflict   (restore this conflict to its original state and start over)\n",
        file=sys.stderr,
        flush=True,
    )
    print(f"Rejected command: {rendered}", file=sys.stderr, flush=True)
    return 1


def _fail_note_required(state: RebaseState, command: str, flag: str) -> int:
    state.emit_phase(
        "rebase",
        "stderr",
        f"Rejected paused rebase command missing required {flag}: {command}",
    )
    print(
        f'git: {command} requires {flag} "<message>".\n\n'
        + "Forklift records this note so the host can summarize how the rebase was\n"
        + "resolved. Provide a non-empty message describing what changed and why,\n"
        + f'then retry, e.g.:\n  {command} {flag} "<message>"\n',
        file=sys.stderr,
        flush=True,
    )
    return 1


def _report_and_block(
    state: RebaseState,
    config: HarnessConfig,
    *,
    action: str,
    sha: str,
    subject: str,
    files: tuple[str, ...],
    note: str,
    git_exit: int,
) -> int:
    """Report a completed transition and block for the orchestrator directive."""

    completed = not state.rebase_in_progress()
    report = TransitionReport(
        action=action,
        sha=sha,
        subject=subject,
        files=files,
        note=note,
        advanced=True,
        completed=completed,
    )
    directive = send_report_and_wait(
        config.control_sock, report, timeout=_recv_timeout()
    )
    if directive is None:
        state.emit_phase(
            "rebase",
            "stderr",
            "No orchestrator directive received; failing closed.",
        )
        return git_exit if git_exit != 0 else 1
    return git_exit


def _emit_continue_failure(
    state: RebaseState, first_line: str, exit_code: int, status_snapshot: str
) -> None:
    command_text = state.continue_check_command_text()
    message = (
        f"{first_line}\n\n"
        f"Command:\n{command_text}\n\n"
        f"Exit code:\n{exit_code}\n\n"
        f"Workspace state after check:\n{status_snapshot}\n\n"
        "Resolve state, then retry rebase continue."
    )
    state.emit_phase("rebase", "stderr", "Blocking git rebase --continue")
    print(message, file=sys.stderr, flush=True)
    state.log_block("rebase", message)


def _handle_continue(
    state: RebaseState, config: HarnessConfig, note: str | None
) -> int:
    if not note:
        return _fail_note_required(state, "git rebase --continue", RESOLUTION_NOTE_FLAG)

    state.emit_phase("rebase", "stdout", "Intercepted git rebase --continue")
    identity = state.rebase_head_identity()
    sha, subject = identity if identity else ("", "")
    progress = state.read_progress()
    files = progress.files if progress else ()
    step = progress.step if progress else 0
    total = progress.total if progress else 0

    if state.is_clean_empty_stop():
        # The agent resolved this conflict into a mechanically empty commit; git
        # would refuse `--continue`, so the harness drops it. Record it as the
        # resolution the agent intended (with their note), and surface a failed
        # auto-skip rather than masking it as a successful advance.
        skip_exit = state.auto_skip_clean_empty_stop()
        return _report_and_block(
            state,
            config,
            action="continue",
            sha=sha,
            subject=subject,
            files=files,
            note=note,
            git_exit=skip_exit,
        )

    if state.has_continue_check():
        before = state.capture_status_snapshot()
        state.emit_phase("rebase", "stdout", "Running frozen rebase continue check")
        check = state.run_continue_check()
        after = state.capture_status_snapshot()
        if check.exit_code != 0:
            _emit_continue_failure(
                state, "Rebase continue check failed.", check.exit_code, after
            )
            return 1
        if after != before:
            _emit_continue_failure(
                state, "Rebase continue check changed workspace state.", 0, after
            )
            return 1
        state.emit_phase(
            "rebase",
            "stdout",
            "Rebase continue check passed with stable workspace state",
        )

    if progress is not None:
        state.emit_event("continue", step, total, sha, subject, files)
    state.emit_phase("rebase", "stdout", "Invoking real git rebase --continue")
    result = state.run_real_git("rebase", "--continue")
    git_exit = result.returncode

    if (
        git_exit != 0
        and state.rebase_in_progress()
        and state.capture_status_snapshot() == ""
    ):
        state.emit_phase(
            "rebase",
            "stdout",
            "Auto-skipping mechanically empty commit after failed continue",
        )
        git_exit = state.auto_skip_clean_empty_stop()
    else:
        state.emit_post_transition_events(total)

    return _report_and_block(
        state,
        config,
        action="continue",
        sha=sha,
        subject=subject,
        files=files,
        note=note,
        git_exit=git_exit,
    )


def _handle_skip(state: RebaseState, config: HarnessConfig, note: str | None) -> int:
    if not note:
        return _fail_note_required(state, "git rebase --skip", RESOLUTION_NOTE_FLAG)

    state.emit_phase("rebase", "stdout", "Intercepted git rebase --skip")
    identity = state.rebase_head_identity()
    if identity is None:
        if state.is_clean_empty_stop():
            skip_exit = state.auto_skip_clean_empty_stop()
            return _report_and_block(
                state,
                config,
                action="skip",
                sha="",
                subject="",
                files=(),
                note=note,
                git_exit=skip_exit,
            )
        state.emit_phase(
            "rebase", "stderr", "Unable to determine REBASE_HEAD for git rebase --skip"
        )
        return 1

    sha, subject = identity
    progress = state.read_progress()
    files = progress.files if progress else ()
    step = progress.step if progress else 0
    total = progress.total if progress else 0
    if progress is not None:
        state.emit_event("skip", step, total, sha, subject, files)
    result = state.run_real_git("rebase", "--skip")
    git_exit = result.returncode
    state.emit_post_transition_events(total)
    return _report_and_block(
        state,
        config,
        action="skip",
        sha=sha,
        subject=subject,
        files=files,
        note=note,
        git_exit=git_exit,
    )


def _handle_abort(state: RebaseState, config: HarnessConfig, reason: str | None) -> int:
    if not reason:
        return _fail_note_required(state, "git rebase --abort", REASON_FLAG)

    state.emit_phase("rebase", "stdout", "Intercepted git rebase --abort")
    identity = state.rebase_head_identity()
    sha, subject = identity if identity else ("", "")
    state.emit_event_from_snapshot("abort")
    result = state.run_real_git("rebase", "--abort")
    git_exit = result.returncode
    return _report_and_block(
        state,
        config,
        action="abort",
        sha=sha,
        subject=subject,
        files=(),
        note=reason,
        git_exit=git_exit,
    )


def _passthrough(state: RebaseState, args: tuple[str, ...]) -> int:
    """Run an allowed read-only git command and forward its output verbatim."""

    result = state.run_real_git(*args)
    if result.stdout:
        _ = sys.stdout.write(result.stdout)
        _ = sys.stdout.flush()
    if result.stderr:
        _ = sys.stderr.write(result.stderr)
        _ = sys.stderr.flush()
    return result.returncode


def _handle_reset(state: RebaseState) -> int:
    """Restore the current paused step to its original conflicted state.

    This is a non-transition: it returns control to the agent in-session without
    contacting the control socket or reporting to the orchestrator.
    """

    state.emit_phase("rebase", "stdout", "Intercepted git reset-conflict")
    outcome = state.reset_current_conflict()
    if not outcome.ok:
        state.emit_phase(
            "rebase", "stderr", f"reset-conflict failed: {outcome.message}"
        )
        print(f"git reset-conflict: {outcome.message}", file=sys.stderr, flush=True)
        return 1
    state.emit_phase("rebase", "stdout", "Reset current conflict to its original state")
    print(
        "Current conflict reset to its original state. Re-resolve the conflicted\n"
        + "files, stage them, then run "
        + f'git rebase --continue {RESOLUTION_NOTE_FLAG} "<what changed & why>".',
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    config = HarnessConfig.from_env()
    state = RebaseState(config)

    if not state.rebase_in_progress():
        return _passthrough(state, tuple(args))

    command = classify_paused_rebase_command(args)
    if command.action == "continue":
        return _handle_continue(state, config, command.resolution_note)
    if command.action == "skip":
        return _handle_skip(state, config, command.resolution_note)
    if command.action == "abort":
        return _handle_abort(state, config, command.reason)
    if command.action == "passthrough":
        return _passthrough(state, command.original_args)
    if command.action == "reset":
        return _handle_reset(state)
    return _fail_unsupported(state, args)


if __name__ == "__main__":
    raise SystemExit(main())
