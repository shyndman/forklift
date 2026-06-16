"""Mode-aware rebase + in-process agent orchestrator launched from `run.sh`.

The orchestrator runs as `forklift`, drives the initial rebase itself (auto-skipping
clean empty stops), then runs the in-process Pydantic AI agent when the rebase pauses
on a real conflict. There is no OpenCode subprocess and no intra-container control
socket: the agent loop and the transition handling share this process, so the two
lifetime modes are control flow (design Decision 5):

  * rebase mode   -> a transition returns the next-conflict state as the tool result
                     and the SAME ``agent.iter`` session resolves the next conflict.
  * conflict mode -> a transition sets ``deps.transition_done``; the driver breaks,
                     and the loop starts a fresh session for the now-current pause,
                     feeding prior resolution notes forward.

It is the sole writer of `harness-state/rebase-report.json` and `usage.json`; no agent
authors `DONE.md`/`STUCK.md`. Stuck runs still exit 0 with `harness-status=completed`
so the host owns the exit-4 decision from the report.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

from .agent import build_agent, resolve_model
from .agent_deps import AgentDeps, RunReport, drive_until_transition
from .logging_setup import configure_logging
from .rebase_state import HarnessConfig, RebaseState

# Default wall-clock budget for the whole agent phase (overridable via env). The
# container watchdog and the host timeout are the ultimate backstops.
DEFAULT_AGENT_TIMEOUT_SECONDS = 600


class Orchestrator:
    """Drives the rebase and the in-process per-mode agent lifecycle."""

    def __init__(
        self,
        config: HarnessConfig,
        state: RebaseState,
        agent: Agent[AgentDeps, str],
        model_id: str,
    ) -> None:
        self.config: HarnessConfig = config
        self.state: RebaseState = state
        self.agent: Agent[AgentDeps, str] = agent
        self.model_id: str = model_id
        self.report: RunReport = RunReport()
        self.deps: AgentDeps = AgentDeps(state=state, config=config, report=self.report)
        self.total_usage: RunUsage = RunUsage()
        self.agent_timeout: int = _env_int(
            "FORKLIFT_AGENT_TIMEOUT", DEFAULT_AGENT_TIMEOUT_SECONDS
        )

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

    def _write_usage(self) -> None:
        """Write aggregated token usage + model id for the host to price exactly."""

        usage_path = self.config.harness_state_dir / "usage.json"
        usage = self.total_usage
        payload: dict[str, object] = {
            "model": self.model_id,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "total_tokens": usage.total_tokens,
            "requests": usage.requests,
            "tool_calls": usage.tool_calls,
        }
        try:
            _ = usage_path.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            self.state.emit_phase("agent", "stderr", f"Unable to write usage: {exc}")

    def finalize_completed(self) -> int:
        self._write_report("completed")
        self._write_usage()
        self._write_harness_status(
            "completed", "agent", "Rebase completed successfully"
        )
        self.state.emit_phase("agent", "stdout", "Rebase completed successfully")
        return 0

    def finalize_stuck(self) -> int:
        self._write_report("stuck")
        self._write_usage()
        self._write_harness_status(
            "completed", "agent", "Rebase aborted; see rebase-report.json"
        )
        self.state.emit_phase("agent", "stdout", "Rebase aborted; reported as stuck")
        return 0

    def finalize_timeout(self) -> int:
        if self.report.stuck is None:
            identity = self.state.rebase_head_identity()
            sha, subject = identity if identity is not None else ("", "")
            self.report.record_abort(
                sha,
                subject,
                f"Agent timed out after {self.agent_timeout}s without completing the rebase",
            )
        self._write_report("stuck")
        self._write_usage()
        self._write_harness_status("failed", "agent", "Agent timed out")
        self.state.emit_phase("agent", "stderr", "Agent timed out")
        return 2

    def finalize_failed(self, message: str) -> int:
        self._write_usage()
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
        """Compose the agent run prompt from instructions + fork context."""

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
        return payload

    # ----- agent loop -----------------------------------------------------

    async def run_agent_loop(self) -> int:
        """Run the agent until the rebase completes, aborts, or the budget elapses.

        Each iteration runs one ``agent.iter`` session via the end-run driver. A
        terminal transition (complete/abort) sets ``deps.terminal``; a conflict-mode
        advance sets ``deps.transition_done`` and the loop relaunches a fresh session;
        a session that ends with the rebase still paused is a failure (the agent gave
        up). rebase mode never flips ``transition_done`` on a plain advance, so its
        single session runs until the rebase finishes.
        """

        deadline = time.monotonic() + self.agent_timeout
        try:
            while True:
                if not self.state.rebase_in_progress():
                    return self.finalize_completed()

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.finalize_timeout()

                payload = self.build_payload()
                try:
                    usage = await asyncio.wait_for(
                        drive_until_transition(self.agent, payload, self.deps),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    self.total_usage = self.total_usage + self.deps.session_usage
                    return self.finalize_timeout()
                self.total_usage = self.total_usage + usage

                if self.deps.terminal is not None:
                    if self.report.stuck is not None:
                        return self.finalize_stuck()
                    return self.finalize_completed()

                if not self.state.rebase_in_progress():
                    return self.finalize_completed()

                if self.deps.transition_done:
                    # Conflict-mode advance: relaunch a fresh session.
                    continue

                # The session ended with the rebase still paused and no transition:
                # the agent gave up. Fail closed.
                return self.finalize_failed(
                    "Agent session ended without completing the rebase"
                )
        finally:
            self._write_usage()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def main() -> int:
    configure_logging()
    config = HarnessConfig.from_env()
    state = RebaseState(config)
    model = resolve_model()
    model_id = (
        model if isinstance(model, str) else getattr(model, "model_name", str(model))
    )
    agent = build_agent(model=model)
    orchestrator = Orchestrator(config, state, agent, model_id)

    outcome = orchestrator.run_initial_rebase()
    if outcome == "completed":
        return orchestrator.finalize_completed()
    if outcome == "failed":
        return orchestrator.finalize_failed("Initial rebase failed")
    return asyncio.run(orchestrator.run_agent_loop())


if __name__ == "__main__":
    raise SystemExit(main())
