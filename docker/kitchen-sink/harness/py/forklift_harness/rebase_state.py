"""Rebase introspection, policy, and host-event emission for the in-container harness.

This module is the Python port of the introspection/policy half of the legacy
`includes/rebase.sh`. It owns everything that reads or mediates the paused rebase
without deciding agent lifetime: real-git invocation with a scrubbed environment,
rebase-in-progress detection, progress snapshots, clean-empty-stop detection, the
frozen continue-check runner, paused-command classification, and the host-facing
rebase progress records emitted as structlog logs over `FORKLIFT_LOG_SOCK`.

Lifetime decisions (kill-and-relaunch vs reply-and-continue) live in
`orchestrate.py`; the per-transition git shim entrypoint lives in `mediate.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import structlog

logger = cast("structlog.typing.FilteringBoundLogger", structlog.get_logger(__name__))

# Read-only git subcommands the agent may run while a rebase is paused.
ALLOWED_PAUSED_COMMANDS = frozenset(
    {"add", "checkout", "diff", "log", "merge-file", "rev-parse", "show", "status"}
)

# Mediated rebase vocabulary flags. These are not git-native; the mediator strips
# them before invoking the real git binary and forwards the value over the control
# socket instead.
RESOLUTION_NOTE_FLAG = "--resolution-note"
REASON_FLAG = "--reason"

# Standalone mediated verb that restores the current paused step to git's original
# conflicted state. Not git-native; the mediator handles it without invoking rebase.
RESET_CONFLICT_COMMAND = "reset-conflict"

# Filesystem path where the pristine conflict index is snapshotted at each pause
# so `git reset-conflict` can restore the step after the agent has mutated it.
DEFAULT_CONFLICT_INDEX_SNAPSHOT = "/run/forklift/conflict-index"


# Trailing sentinel the harness appends to every preamble line it injects into the
# frozen continue-check file (see includes/rebase.sh:write_rebase_continue_check_file,
# which must keep this string in sync). `continue_check_command_text` strips the
# leading shebang plus any preamble line carrying this marker, so failure logs show
# only the fork's own commands without hard-coding a preamble line count.
CONTINUE_CHECK_PREAMBLE_MARKER = "forklift:continue-check-preamble"


@dataclass(frozen=True)
class HarnessConfig:
    """Runtime paths and identity shared by the mediator and orchestrator."""

    workspace_dir: Path
    harness_state_dir: Path
    real_git_bin: str
    main_branch: str
    upstream_ref: str
    continue_check_file: Path
    agent_lifetime: str
    git_user_name: str = "Forklift Agent"
    git_user_email: str = "forklift@github.com"
    git_editor: str = "true"
    conflict_index_snapshot: Path = Path(DEFAULT_CONFLICT_INDEX_SNAPSHOT)

    @classmethod
    def from_env(cls) -> HarnessConfig:
        """Build configuration from the harness runtime environment."""

        workspace = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
        harness_state = Path(os.environ.get("HARNESS_STATE_DIR", "/harness-state"))
        real_git = os.environ.get("REAL_GIT_BIN", "/usr/bin/git")
        main_branch = os.environ.get("FORKLIFT_MAIN_BRANCH", "main")
        upstream_ref = os.environ.get("UPSTREAM_REF", f"upstream/{main_branch}")
        continue_check = Path(
            os.environ.get(
                "REBASE_CONTINUE_CHECK_FILE",
                str(harness_state / "rebase-continue-check.sh"),
            )
        )
        agent_lifetime = os.environ.get("FORKLIFT_AGENT_LIFETIME", "conflict")
        return cls(
            workspace_dir=workspace,
            harness_state_dir=harness_state,
            real_git_bin=real_git,
            main_branch=main_branch,
            upstream_ref=upstream_ref,
            continue_check_file=continue_check,
            agent_lifetime=agent_lifetime,
            git_user_name=os.environ.get("FORKLIFT_GIT_USER_NAME", "Forklift Agent"),
            git_user_email=os.environ.get(
                "FORKLIFT_GIT_USER_EMAIL", "forklift@github.com"
            ),
            git_editor=os.environ.get("FORKLIFT_GIT_EDITOR", "true"),
            conflict_index_snapshot=Path(
                os.environ.get(
                    "FORKLIFT_CONFLICT_INDEX_SNAPSHOT", DEFAULT_CONFLICT_INDEX_SNAPSHOT
                )
            ),
        )


@dataclass(frozen=True)
class RebaseProgress:
    """Snapshot of an in-progress rebase used to build host events."""

    step: int
    total: int
    sha: str
    subject: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class ContinueCheckResult:
    """Outcome of running the frozen fork-supplied rebase continue check."""

    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ResetOutcome:
    """Outcome of a `git reset-conflict` restore of the current paused step."""

    ok: bool
    message: str


@dataclass(frozen=True)
class PausedCommand:
    """Structured classification of a git command issued during a paused rebase."""

    action: str  # continue | skip | abort | passthrough | unsupported | reset
    resolution_note: str | None
    reason: str | None
    original_args: tuple[str, ...]


def _sanitize_note(value: str) -> str:
    """Strip control characters from note/reason text bound for the report and logs."""

    cleaned = "".join(
        char for char in value if ord(char) >= 32 and ord(char) != 127
    ).strip()
    return cleaned


def classify_paused_rebase_command(args: list[str]) -> PausedCommand:
    """Classify a paused-rebase git invocation into a mediated action.

    Mirrors the legacy bash `normalize`/`classify` rejections (config overrides,
    aliases, alternate exec paths, unknown commands) and additionally recognizes
    the mediated `--resolution-note`/`--reason` vocabulary, stripping those flags
    from the git-native token stream.
    """

    normalized: list[str] = []
    has_config_override = False
    resolution_note: str | None = None
    reason: str | None = None

    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "-c":
            has_config_override = True
            index += 1  # skip the key=value token
        elif arg.startswith("-c"):
            has_config_override = True
        elif arg == "--config-env":
            has_config_override = True
            index += 1  # skip the env-var name token
        elif (
            arg.startswith("--config-env=")
            or arg == "--exec-path"
            or arg.startswith("--exec-path=")
        ):
            has_config_override = True
        elif arg == RESOLUTION_NOTE_FLAG:
            index += 1
            resolution_note = args[index] if index < len(args) else ""
        elif arg.startswith(f"{RESOLUTION_NOTE_FLAG}="):
            resolution_note = arg[len(RESOLUTION_NOTE_FLAG) + 1 :]
        elif arg == REASON_FLAG:
            index += 1
            reason = args[index] if index < len(args) else ""
        elif arg.startswith(f"{REASON_FLAG}="):
            reason = arg[len(REASON_FLAG) + 1 :]
        elif arg in ("--continue", "--skip", "--abort"):
            normalized.append(arg)
        elif (
            arg == "rebase"
            or arg == RESET_CONFLICT_COMMAND
            or arg in ALLOWED_PAUSED_COMMANDS
        ):
            normalized.append(arg)
        index += 1

    note = _sanitize_note(resolution_note) if resolution_note is not None else None
    reason_text = _sanitize_note(reason) if reason is not None else None
    original = tuple(args)

    if has_config_override:
        return PausedCommand("unsupported", note, reason_text, original)

    command_name = normalized[0] if normalized else ""
    if not command_name:
        return PausedCommand("unsupported", note, reason_text, original)

    if command_name == RESET_CONFLICT_COMMAND:
        if len(normalized) != 1:
            return PausedCommand("unsupported", note, reason_text, original)
        return PausedCommand("reset", note, reason_text, original)

    if command_name != "rebase":
        if command_name in ALLOWED_PAUSED_COMMANDS:
            return PausedCommand("passthrough", note, reason_text, original)
        return PausedCommand("unsupported", note, reason_text, original)

    if len(normalized) == 2 and normalized[0] == "rebase":
        action = {"--continue": "continue", "--skip": "skip", "--abort": "abort"}.get(
            normalized[1]
        )
        if action is not None:
            return PausedCommand(action, note, reason_text, original)

    return PausedCommand("unsupported", note, reason_text, original)


class RebaseState:
    """Stateless-per-call introspection helpers bound to a `HarnessConfig`."""

    def __init__(self, config: HarnessConfig) -> None:
        self.config: HarnessConfig = config

    # ----- logging --------------------------------------------------------

    def emit_phase(self, phase: str, stream: str, message: str) -> None:
        """Mirror the bash phase-message helper: print to the console stream."""

        target = sys.stderr if stream == "stderr" else sys.stdout
        print(f"[{phase}] {message}", file=target, flush=True)

    def log_block(self, phase: str, text: str) -> None:
        """Print a multi-line block to stdout under a phase prefix."""

        for line in text.splitlines() or [""]:
            print(f"[{phase}] {line}", flush=True)

    # ----- real git -------------------------------------------------------

    def _git_env(self) -> dict[str, str]:
        """Scrubbed environment for the real git binary (mirrors bash run_real_git)."""

        home = os.environ.get("HOME", "/home/forklift")
        lang = os.environ.get("LANG", "C.UTF-8")
        return {
            "HOME": home,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": lang,
            "LC_ALL": os.environ.get("LC_ALL", lang),
            "TERM": os.environ.get("TERM", "dumb"),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_COMMITTER_NAME": self.config.git_user_name,
            "GIT_COMMITTER_EMAIL": self.config.git_user_email,
            "GIT_EDITOR": self.config.git_editor,
        }

    def run_real_git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Invoke the real git binary in the workspace with a scrubbed environment."""

        return subprocess.run(
            [self.config.real_git_bin, *args],
            cwd=str(self.config.workspace_dir),
            env=self._git_env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def _git_stdout(self, *args: str) -> str:
        """Return trimmed stdout for a real-git command, empty on failure."""

        result = self.run_real_git(*args)
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    # ----- introspection --------------------------------------------------

    def rebase_in_progress(self) -> bool:
        """Return whether the workspace has an in-progress rebase."""

        git_dir = self.config.workspace_dir / ".git"
        return (git_dir / "rebase-merge").is_dir() or (
            git_dir / "rebase-apply"
        ).is_dir()

    def capture_status_snapshot(self) -> str:
        """Return porcelain v1 status including all untracked files."""

        result = self.run_real_git("status", "--porcelain=v1", "--untracked-files=all")
        return result.stdout

    def rebase_head_identity(self) -> tuple[str, str] | None:
        """Return `(sha, subject)` for the current REBASE_HEAD, or None."""

        sha = self._git_stdout("rev-parse", "REBASE_HEAD")
        subject = self._git_stdout("show", "-s", "--format=%s", "REBASE_HEAD")
        if sha and subject:
            return sha, subject
        return None

    def is_clean_empty_stop(self) -> bool:
        """Return whether the rebase paused with a clean (mechanically empty) tree."""

        if not self.rebase_in_progress():
            return False
        return self.capture_status_snapshot() == ""

    def count_rebase_commits(self) -> int:
        """Count commits between the upstream ref and HEAD for completion events."""

        raw = self._git_stdout(
            "rev-list", "--count", f"{self.config.upstream_ref}..HEAD"
        )
        try:
            return int(raw)
        except ValueError:
            return 0

    def read_progress(self) -> RebaseProgress | None:
        """Read the current rebase progress snapshot, or None when not paused."""

        git_dir = self.config.workspace_dir / ".git"
        if (git_dir / "rebase-merge").is_dir():
            state_dir = git_dir / "rebase-merge"
            step_file, total_file = "msgnum", "end"
        elif (git_dir / "rebase-apply").is_dir():
            state_dir = git_dir / "rebase-apply"
            step_file, total_file = "next", "last"
        else:
            return None

        try:
            step_raw = (state_dir / step_file).read_text(encoding="utf-8").strip()
            total_raw = (state_dir / total_file).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not step_raw or not total_raw:
            return None

        sha = self._git_stdout("rev-parse", "REBASE_HEAD")
        subject = self._git_stdout("show", "-s", "--format=%s", "REBASE_HEAD")
        files_raw = self._git_stdout("diff", "--name-only", "--diff-filter=U")
        files = tuple(line for line in files_raw.splitlines() if line)

        try:
            step = int(step_raw)
            total = int(total_raw)
        except ValueError:
            return None
        return RebaseProgress(
            step=step, total=total, sha=sha, subject=subject, files=files
        )

    # ----- continue check -------------------------------------------------

    def run_continue_check(self) -> ContinueCheckResult:
        """Run the frozen fork-supplied continue check in the workspace."""

        completed = subprocess.run(
            ["bash", str(self.config.continue_check_file)],
            cwd=str(self.config.workspace_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        return ContinueCheckResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def continue_check_command_text(self) -> str:
        """Return the human-readable continue-check command body for failure logs."""

        try:
            lines = self.config.continue_check_file.read_text(
                encoding="utf-8"
            ).splitlines()
        except OSError:
            return ""
        # Drop the leading harness preamble: a shebang on the first line, then any
        # contiguous lines carrying the preamble marker. Stop at the first line the
        # harness did not generate so the fork's own commands are shown verbatim.
        body_start = 0
        for index, line in enumerate(lines):
            if index == 0 and line.startswith("#!"):
                body_start = index + 1
                continue
            if CONTINUE_CHECK_PREAMBLE_MARKER in line:
                body_start = index + 1
                continue
            break
        return "\n".join(lines[body_start:])

    def has_continue_check(self) -> bool:
        """Return whether a non-empty frozen continue check is installed."""

        try:
            return self.config.continue_check_file.stat().st_size > 0
        except OSError:
            return False

    # ----- host events ----------------------------------------------------

    def _emit_progress_record(
        self,
        event: str,
        step: int,
        total: int,
        sha: str,
        subject: str,
        files: tuple[str, ...],
    ) -> None:
        """Log one rebase-progress record (authored in-container, shipped via structlog)."""

        fields: dict[str, object] = {"step": step, "total": total}
        if sha:
            fields["sha"] = sha
        if subject:
            fields["subject"] = subject
        if files:
            fields["files"] = list(files)

        if event == "conflict":
            logger.warning(
                f"conflict {step}/{total}", conflict_files=len(files), **fields
            )
        elif event == "complete":
            logger.info("rebase complete", **fields)
        elif event == "auto_skip":
            logger.info(f"auto-skip {step}/{total}", **fields)
        elif event == "reset":
            logger.info(f"reset conflict {step}/{total}", **fields)
        else:
            logger.info(f"rebase {step}/{total}", **fields)

    def emit_event_from_snapshot(self, event: str) -> None:
        """Emit a progress/conflict record built from the current snapshot, if any."""

        progress = self.read_progress()
        if progress is None:
            return
        self._emit_progress_record(
            event,
            progress.step,
            progress.total,
            progress.sha,
            progress.subject,
            progress.files,
        )

    def emit_paused_events(self) -> None:
        """Emit the progress + conflict records for a freshly paused rebase.

        Also captures a pristine byte copy of the conflict index first, so a later
        `git reset-conflict` can restore this step after the agent has mutated it.
        """

        self.snapshot_conflict_index()
        self.emit_event_from_snapshot("progress")
        self.emit_event_from_snapshot("conflict")

    def emit_complete_event(self, total: int) -> None:
        """Emit the terminal completion record for a finished rebase."""

        if total <= 0:
            return
        self._emit_progress_record("complete", total, total, "", "", ())

    def emit_post_transition_events(self, total: int) -> None:
        """Emit paused records when still rebasing, else the completion record."""

        if self.rebase_in_progress():
            self.emit_paused_events()
            return
        self.emit_complete_event(total if total > 0 else self.count_rebase_commits())

    # ----- shared actions -------------------------------------------------

    def _snapshot_meta_path(self) -> Path:
        """Sidecar path recording which paused step the snapshot belongs to."""

        snapshot = self.config.conflict_index_snapshot
        return snapshot.parent / f"{snapshot.name}.meta"

    def _current_step_identity(self) -> str | None:
        """Signature identifying the paused step a snapshot would belong to.

        Built from REBASE_HEAD (the commit being applied) plus the step counters, so
        a snapshot captured at one conflict can never be mistaken for another. Returns
        None when no resolvable progress exists.
        """

        progress = self.read_progress()
        if progress is None or not progress.sha:
            return None
        return f"{progress.step}/{progress.total}:{progress.sha}"

    def _read_snapshot_identity(self) -> str | None:
        """Return the step identity recorded alongside the snapshot, or None."""

        try:
            return self._snapshot_meta_path().read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def _discard_snapshot(self) -> None:
        """Remove the snapshot and its identity sidecar, ignoring missing files."""

        for path in (self.config.conflict_index_snapshot, self._snapshot_meta_path()):
            try:
                path.unlink()
            except OSError:
                pass

    def snapshot_conflict_index(self) -> None:
        """Capture a pristine byte copy of `.git/index` at the current pause.

        Stamped with the current step identity so `git reset-conflict` can prove the
        snapshot belongs to the conflict in front of the agent before restoring it.
        Best-effort: once the agent runs `git add`, the unmerged stages collapse and
        cannot be reconstructed, so this byte snapshot is the only `add`-proof way to
        restore the conflicted step later. A failed (or unidentifiable) capture
        discards any prior snapshot so a later reset fails closed rather than
        restoring a stale step's index. Failures must not break the rebase.
        """

        identity = self._current_step_identity()
        if identity is None:
            self._discard_snapshot()
            self.emit_phase(
                "rebase",
                "stderr",
                "Unable to snapshot conflict index: step identity unavailable",
            )
            return

        src = self.config.workspace_dir / ".git" / "index"
        dest = self.config.conflict_index_snapshot
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copyfile(src, dest)
            _ = self._snapshot_meta_path().write_text(identity, encoding="utf-8")
        except OSError as exc:
            self._discard_snapshot()
            self.emit_phase(
                "rebase", "stderr", f"Unable to snapshot conflict index: {exc}"
            )

    def reset_current_conflict(self) -> ResetOutcome:
        """Restore the current paused step to git's original conflicted state.

        Restores the snapshotted pristine index and repaints the working tree from
        its merge stages, discarding the agent's in-progress resolution. Tracked-only:
        untracked files the agent created are left in place. The snapshot's recorded
        step identity must match the current conflict, or the restore is refused so a
        stale snapshot can never overwrite the tree with another commit's stages.
        """

        if not self.rebase_in_progress():
            return ResetOutcome(False, "no rebase is in progress")
        snapshot = self.config.conflict_index_snapshot
        if not snapshot.is_file():
            return ResetOutcome(
                False, "no snapshot of the original conflict was captured"
            )
        expected = self._current_step_identity()
        if expected is None or self._read_snapshot_identity() != expected:
            return ResetOutcome(
                False, "the captured snapshot does not match the current conflict"
            )
        index_path = self.config.workspace_dir / ".git" / "index"
        try:
            _ = shutil.copyfile(snapshot, index_path)
        except OSError as exc:
            return ResetOutcome(False, f"could not restore the conflict index: {exc}")
        result = self.run_real_git("checkout", "-m", "--", ".")
        if result.returncode != 0:
            return ResetOutcome(False, f"git checkout failed: {result.stderr.strip()}")
        self.emit_event_from_snapshot("reset")
        return ResetOutcome(True, "")

    def auto_skip_clean_empty_stop(self) -> int:
        """Auto-skip a clean (mechanically empty) rebase stop via the real git binary.

        This path is exempt from the mediated `--resolution-note` vocabulary because
        it is driven by the harness, not the agent.
        """

        total = 0
        skipped_sha = ""
        progress = self.read_progress()
        if progress is not None:
            total = progress.total
            skipped_sha = progress.sha
            self._emit_progress_record(
                "auto_skip",
                progress.step,
                progress.total,
                progress.sha,
                progress.subject,
                progress.files,
            )
        self.emit_phase("rebase", "stdout", "Auto-skipping clean empty rebase stop")
        result = self.run_real_git("rebase", "--skip")
        # `git rebase --skip` exits non-zero whenever it pauses again on the next
        # conflict -- a successful advance, not a failure -- so emit based on the
        # resulting rebase state, not the exit code. Stay silent only when the skip
        # did not advance (still paused on the same commit).
        if not self.rebase_in_progress():
            self.emit_post_transition_events(total)
        else:
            new_identity = self.rebase_head_identity()
            new_sha = new_identity[0] if new_identity else ""
            if new_sha and new_sha != skipped_sha:
                self.emit_post_transition_events(total)
        return result.returncode
