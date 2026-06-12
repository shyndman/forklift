"""Mode-aware rebase + agent orchestrator launched from `run.sh`.

The orchestrator runs as `forklift`, drives the initial rebase itself (auto-skipping
clean empty stops), then enters an agent loop when the rebase pauses on a real
conflict. It binds the intra-container control socket and, per reported transition,
applies the only lifetime branch:

  * rebase mode   -> reply `proceed`; the same agent session resolves the next conflict.
  * conflict mode -> kill the agent process group and relaunch a fresh session for
                     the now-current pause, feeding prior resolution notes forward.

It is the sole writer of `harness-state/rebase-report.json`; no agent authors
`DONE.md`/`STUCK.md` anymore. Stuck runs still exit 0 with `harness-status=completed`
so the host owns the exit-4 decision from the report.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .control import PROCEED, ControlListener, Directive, TransitionReport
from .rebase_state import HarnessConfig, RebaseState

DEFAULT_OPENCODE_BIN = "/opt/opencode/bin/opencode"
CONTROL_RECV_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class _TransitionOutcome:
    """Result of handling one transition: a terminal exit code or a relaunch signal."""

    terminal: int | None
    relaunch: bool


@dataclass
class RunReport:
    """Accumulated rebase outcome written to `rebase-report.json`."""

    resolutions: list[dict[str, str]] = field(default_factory=list)
    skips: list[dict[str, str]] = field(default_factory=list)
    stuck: dict[str, str] | None = None

    def record(self, report: TransitionReport) -> None:
        if report.action == "continue":
            self.resolutions.append(
                {"sha": report.sha, "subject": report.subject, "note": report.note}
            )
        elif report.action == "skip":
            self.skips.append(
                {"sha": report.sha, "subject": report.subject, "note": report.note}
            )
        elif report.action == "abort":
            self.stuck = {
                "sha": report.sha,
                "subject": report.subject,
                "reason": report.note,
            }

    def to_payload(self, outcome: str) -> dict[str, object]:
        return {
            "outcome": outcome,
            "resolutions": self.resolutions,
            "skips": self.skips,
            "stuck": self.stuck,
        }


class Orchestrator:
    """Drives the rebase and the per-mode agent lifecycle."""

    def __init__(self, config: HarnessConfig, state: RebaseState) -> None:
        self.config: HarnessConfig = config
        self.state: RebaseState = state
        self.report: RunReport = RunReport()
        self.opencode_timeout: int = _env_int("OPENCODE_TIMEOUT", 600)
        self.server_port: int = _env_int("OPENCODE_SERVER_PORT", 4096)
        self.opencode_bin: str = os.environ.get("OPENCODE_BIN", DEFAULT_OPENCODE_BIN)
        self.model: str = os.environ.get("OPENCODE_MODEL", "")
        self.variant: str = os.environ.get("OPENCODE_VARIANT", "")

    # ----- status + report writers ---------------------------------------

    def _write_harness_status(self, status: str, phase: str, message: str) -> None:
        status_file = self.config.harness_state_dir / "harness-status.txt"
        try:
            _ = status_file.write_text(
                f"status={status}\nphase={phase}\nmessage={message}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self.state.emit_phase(
                "agent", "stderr", f"Unable to write harness status: {exc}"
            )

    def _write_report(self, outcome: str) -> None:
        report_path = self.config.harness_state_dir / "rebase-report.json"
        try:
            _ = report_path.write_text(
                json.dumps(self.report.to_payload(outcome), indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self.state.emit_phase(
                "agent", "stderr", f"Unable to write rebase report: {exc}"
            )

    def finalize_completed(self) -> int:
        self._write_report("completed")
        self._write_harness_status(
            "completed", "agent", "Rebase completed successfully"
        )
        self.state.emit_phase("agent", "stdout", "Rebase completed successfully")
        return 0

    def finalize_stuck(self) -> int:
        self._write_report("stuck")
        self._write_harness_status(
            "completed", "agent", "Rebase aborted; see rebase-report.json"
        )
        self.state.emit_phase("agent", "stdout", "Rebase aborted; reported as stuck")
        return 0

    def finalize_timeout(self) -> int:
        self._write_harness_status("failed", "agent", "Agent timed out")
        self.state.emit_phase("agent", "stderr", "Agent timed out")
        return 2

    def finalize_failed(self, message: str) -> int:
        self._write_harness_status("failed", "agent", message)
        self.state.emit_phase("agent", "stderr", message)
        return 1

    # ----- initial rebase -------------------------------------------------

    def run_initial_rebase(self) -> str:
        """Run the rebase onto upstream, auto-skipping clean empty stops.

        Returns one of ``completed``, ``paused``, or ``failed``.
        """

        total = self.state.count_rebase_commits()
        self.state.emit_phase(
            "rebase",
            "stdout",
            f"Starting initial rebase onto {self.config.upstream_ref}",
        )
        result = self.state.run_real_git("rebase", self.config.upstream_ref)
        if result.stdout.strip():
            self.state.log_block("rebase", result.stdout.strip())
        if result.stderr.strip():
            self.state.log_block("rebase", result.stderr.strip())

        if result.returncode == 0:
            if self.state.rebase_in_progress():
                self.state.emit_phase(
                    "rebase",
                    "stderr",
                    "Initial rebase reported success but left rebase state behind",
                )
                return "failed"
            self.state.emit_phase(
                "rebase", "stdout", "Initial rebase completed cleanly"
            )
            self.state.emit_complete_event(total)
            return "completed"

        if not self.state.rebase_in_progress():
            self.state.emit_phase(
                "rebase",
                "stderr",
                "Initial rebase failed before entering a paused rebase state",
            )
            return "failed"

        while self.state.rebase_in_progress() and self.state.is_clean_empty_stop():
            if self.state.auto_skip_clean_empty_stop() != 0:
                self.state.emit_phase(
                    "rebase",
                    "stderr",
                    "Initial rebase could not auto-skip clean empty rebase stop",
                )
                return "failed"

        if not self.state.rebase_in_progress():
            self.state.emit_phase(
                "rebase",
                "stdout",
                "Initial rebase completed after auto-skipping clean empty stops",
            )
            return "completed"

        self.state.emit_paused_events()
        self.state.emit_phase("rebase", "stdout", "Initial rebase paused on conflicts")
        return "paused"

    # ----- agent payload --------------------------------------------------

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def build_payload(self) -> str:
        """Compose the agent prompt, appending prior notes in conflict mode."""

        instructions = self._read_file(
            Path(
                os.environ.get(
                    "INSTRUCTIONS_FILE",
                    str(self.config.harness_state_dir / "instructions.txt"),
                )
            )
        )
        fork_context = self._read_file(
            Path(
                os.environ.get(
                    "FORK_CONTEXT_FILE",
                    str(self.config.harness_state_dir / "fork-context.md"),
                )
            )
        )
        payload = f"{instructions}\n\n{fork_context}"

        if self.config.agent_lifetime == "conflict" and (
            self.report.resolutions or self.report.skips
        ):
            payload += "\n\n" + self._continuity_section()
        return payload

    def _continuity_section(self) -> str:
        lines = [
            "== Prior conflicts in this rebase ==",
            "Earlier conflicts were resolved by previous agents. For context only:",
        ]
        for entry in self.report.resolutions:
            lines.append(
                f"- resolved {entry['sha']} {entry['subject']}: {entry['note']}"
            )
        for entry in self.report.skips:
            lines.append(
                f"- skipped {entry['sha']} {entry['subject']}: {entry['note']}"
            )
        return "\n".join(lines)

    # ----- opencode process management -----------------------------------

    def _opencode_command(self, payload: str, remaining: int) -> list[str]:
        command = [
            "timeout",
            str(remaining),
            self.opencode_bin,
            "run",
            "--attach",
            f"http://127.0.0.1:{self.server_port}",
            "--log-level",
            "DEBUG",
            "--format",
            "json",
            "--dir",
            str(self.config.workspace_dir),
        ]
        if self.model:
            command += ["--model", self.model]
        command += ["--variant", self.variant, payload]
        return command

    def launch_opencode(self, payload: str, remaining: int) -> subprocess.Popen[bytes]:
        command = self._opencode_command(payload, remaining)
        self.state.log_client(
            f"Launching OpenCode client (model={self.model or '(default)'} "
            + f"variant={self.variant} remaining={remaining}s)"
        )
        log_handle = self.config.client_log.open("ab")
        return subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def _kill_process_group(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            _ = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    # ----- agent loop -----------------------------------------------------

    def run_agent_loop(self) -> int:
        with ControlListener(self.config.control_sock) as listener:
            deadline = time.monotonic() + self.opencode_timeout
            proc: subprocess.Popen[bytes] | None = None
            try:
                while True:
                    remaining = int(deadline - time.monotonic())
                    if remaining <= 0:
                        if proc is not None:
                            self._kill_process_group(proc)
                        return self.finalize_timeout()

                    if proc is None:
                        proc = self.launch_opencode(self.build_payload(), remaining)

                    conn = listener.accept()
                    if conn is None:
                        if proc.poll() is not None:
                            outcome = self._handle_agent_exit()
                            if outcome is not None:
                                return outcome
                            # Rebase still in progress and agent gone: fail closed.
                            return self.finalize_failed(
                                "Agent exited without completing the rebase"
                            )
                        continue

                    outcome = self._handle_transition(listener, conn, proc)
                    if outcome.terminal is not None:
                        self.wait_for_exit(proc)
                        return outcome.terminal
                    if outcome.relaunch:
                        # Conflict mode already killed the agent group; relaunch fresh.
                        proc = None
            finally:
                if proc is not None:
                    self._kill_process_group(proc)

    def _handle_agent_exit(self) -> int | None:
        """Resolve an agent process that exited without a terminal report."""

        if not self.state.rebase_in_progress():
            return self.finalize_completed()
        return None

    def _handle_transition(
        self,
        listener: ControlListener,
        connection: socket.socket,
        proc: subprocess.Popen[bytes],
    ) -> _TransitionOutcome:
        """Process one transition report and apply the single lifetime branch.

        Returns a terminal exit code (completed/abort) or a relaunch signal. For a
        conflict-mode advance the agent process group is killed *before* the
        connection closes, so the blocked mediator dies without surfacing a
        misleading failure to the agent.
        """

        report = listener.recv_report(connection, timeout=CONTROL_RECV_TIMEOUT_SECONDS)
        if report is None:
            connection.close()
            return _TransitionOutcome(terminal=None, relaunch=False)

        self.report.record(report)
        self._log_transition(report)

        if report.action == "abort":
            listener.reply(connection, Directive(PROCEED))
            connection.close()
            return _TransitionOutcome(terminal=self.finalize_stuck(), relaunch=False)
        if report.completed:
            listener.reply(connection, Directive(PROCEED))
            connection.close()
            return _TransitionOutcome(
                terminal=self.finalize_completed(), relaunch=False
            )

        if self.config.agent_lifetime == "rebase":
            listener.reply(connection, Directive(PROCEED))
            connection.close()
            return _TransitionOutcome(terminal=None, relaunch=False)

        # Conflict mode: kill the agent group (reaping the blocked mediator) before
        # closing the connection, then relaunch a fresh session for the next pause.
        self._kill_process_group(proc)
        connection.close()
        return _TransitionOutcome(terminal=None, relaunch=True)

    def _log_transition(self, report: TransitionReport) -> None:
        self.state.emit_phase(
            "agent",
            "stdout",
            f"Transition {report.action} {report.sha} {report.subject} "
            + f"(completed={report.completed})",
        )

    def wait_for_exit(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            _ = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._kill_process_group(proc)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def main() -> int:
    config = HarnessConfig.from_env()
    state = RebaseState(config)
    orchestrator = Orchestrator(config, state)

    result = orchestrator.run_initial_rebase()
    if result == "completed":
        return orchestrator.finalize_completed()
    if result == "failed":
        return orchestrator.finalize_failed("Initial rebase failed before agent launch")
    return orchestrator.run_agent_loop()


if __name__ == "__main__":
    raise SystemExit(main())
