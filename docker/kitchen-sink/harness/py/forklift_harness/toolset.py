"""The forklift git-mediation toolset: a `run_command` that intercepts workspace git.

This replaces the stock harness ``Shell`` capability. We expose exactly one tool,
``run_command``, so there is no background-process lifecycle (``start_command`` /
``check_command`` / ``stop_command`` are intentionally absent -- a daemon surviving
a conflict-mode session teardown was a class of nasty bugs). ``run_command``:

* delegates unconditionally when no rebase is paused (nothing to mediate);
* otherwise parses the command (bashlex) to find git invocations, applies the
  target-repo discriminator + ``GIT_*`` rejection, mediates a workspace-repo git
  command through the in-process transition path (serialized behind ``deps.lock``
  for ``asyncio.gather`` fan-out), and delegates everything else to a real shell.

The shell delegate is home-rolled (a plain subprocess) rather than the harness
``ShellToolset``: the released ``pydantic-ai-harness`` (0.3.0) ships only
``CodeMode``, so there is no ``ShellToolset`` to wrap. The agent's shell is the
same constrained container shell it always had.
"""

from __future__ import annotations

import asyncio
import time
from typing import cast

import structlog
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from .agent_deps import AgentDeps
from .command_parse import collect_git_invocations
from .target_repo import GitTarget, resolve_git_target
from .transitions import mediate_workspace_git

# Default wall-clock bound on a single delegated shell command. The container
# watchdog and the agent timeout are the ultimate backstops; this keeps one
# runaway command from consuming the whole budget.
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300.0

# Returned (as ModelRetry) when a command carries a GIT_* override or stacks more
# than one workspace git command -- both ambiguous to mediate, so the agent retries.
_GIT_ENV_RETRY = (
    "Do not set GIT_* environment variables on git commands while a rebase is "
    "paused; run git without them so the harness can mediate the workspace repo."
)
_MULTIPLE_WORKSPACE_GIT_RETRY = (
    "Run one workspace git command per call while the rebase is paused (for "
    "example, stage with `git add` in one call, then `git rebase --continue "
    '--resolution-note "..."` in the next).'
)

logger = cast("structlog.typing.FilteringBoundLogger", structlog.get_logger(__name__))


class ForkliftGitToolset(FunctionToolset[AgentDeps]):
    """A `FunctionToolset` exposing only the git-mediating ``run_command``."""

    def __init__(
        self, *, command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS
    ) -> None:
        super().__init__()
        self._command_timeout: float = command_timeout
        _ = self.add_function(self.run_command)

    async def run_command(self, ctx: RunContext[AgentDeps], command: str) -> str:
        """Run a shell command, mediating any workspace-repo git during a paused rebase.

        Returns the command's combined output (or the mediation result). Raises
        :class:`pydantic_ai.exceptions.ModelRetry` when the command cannot be parsed
        during a paused rebase, carries a ``GIT_*`` override, or stacks multiple
        workspace git commands. Emits exactly one ``"tool exec"`` telemetry event
        per call, recording the command, whether it succeeded, and its duration.
        """

        start = time.perf_counter()
        try:
            result = await self._dispatch(ctx, command)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "tool exec",
                tool="run_command",
                command=command,
                ok=False,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "tool exec",
            tool="run_command",
            command=command,
            ok=True,
            duration_ms=duration_ms,
        )
        return result

    async def _dispatch(self, ctx: RunContext[AgentDeps], command: str) -> str:
        """Mediate workspace git during a paused rebase, else delegate to the shell."""

        deps = ctx.deps
        state = deps.state
        rebase_paused = state.rebase_in_progress()

        # None => no rebase paused; nothing to mediate, delegate unconditionally.
        invocations = collect_git_invocations(command, rebase_paused=rebase_paused)
        if invocations is None:
            return await self._delegate(command, deps)

        workspace_git_dir = state.config.workspace_dir / ".git"
        workspace_args: list[tuple[str, ...]] = []
        for invocation in invocations:
            target = resolve_git_target(
                invocation.args,
                cwd=state.config.workspace_dir,
                env=invocation.env,
                workspace_git_dir=workspace_git_dir,
                real_git_bin=state.config.real_git_bin,
            )
            if target is GitTarget.REJECTED:
                raise ModelRetry(_GIT_ENV_RETRY)
            if target is GitTarget.WORKSPACE:
                workspace_args.append(invocation.args)

        if not workspace_args:
            # All git targets some other repo (test temp repos, tooling git-dirs);
            # pass the whole command through unmediated, mutating verbs included.
            return await self._delegate(command, deps)

        if len(workspace_args) > 1:
            raise ModelRetry(_MULTIPLE_WORKSPACE_GIT_RETRY)

        # Serialize rebase-state mutation so concurrent run_command calls (asyncio
        # fan-out from code mode) never interleave a transition.
        async with deps.lock:
            return mediate_workspace_git(deps, workspace_args[0])

    async def _delegate(self, command: str, deps: AgentDeps) -> str:
        """Execute ``command`` in the workspace shell and return its combined output."""

        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-c",
            command,
            cwd=str(deps.config.workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._command_timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            _ = await process.wait()
            return f"[command timed out after {self._command_timeout:.0f}s]"

        output = stdout.decode("utf-8", errors="replace")
        returncode = process.returncode if process.returncode is not None else -1
        if returncode != 0:
            suffix = f"[exit {returncode}]"
            return f"{output}\n{suffix}" if output else suffix
        return output
