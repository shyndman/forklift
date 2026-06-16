"""Workspace-scoped file tools standing in for the harness ``FileSystem`` capability.

The released ``pydantic-ai-harness`` (0.3.0) ships only ``CodeMode`` -- no
``FileSystem`` -- so the agent's read/write/edit tools are home-rolled here. Every
path is resolved relative to the workspace and confined to it (path-traversal
prevention), matching what the harness ``FileSystem`` would enforce. The agent uses
these to read and rewrite conflicted files during a paused rebase.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from .agent_deps import AgentDeps

# Upper bound on a single read, so a pathological file can't blow the context.
MAX_READ_BYTES = 1_000_000


def _resolve_in_workspace(deps: AgentDeps, path: str) -> Path:
    """Resolve ``path`` under the workspace, rejecting anything that escapes it."""

    base = deps.config.workspace_dir.resolve()
    candidate = Path(path)
    target = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if target != base and base not in target.parents:
        raise ModelRetry(
            f"Path {path!r} is outside the workspace; only workspace files are accessible."
        )
    return target


class FileToolset(FunctionToolset[AgentDeps]):
    """Read, write, and edit tools confined to the workspace repository."""

    def __init__(self) -> None:
        super().__init__()
        _ = self.add_function(self.read_file)
        _ = self.add_function(self.write_file)
        _ = self.add_function(self.edit_file)

    def read_file(self, ctx: RunContext[AgentDeps], path: str) -> str:
        """Return the text of a workspace file (relative or absolute-within-workspace)."""

        target = _resolve_in_workspace(ctx.deps, path)
        try:
            data = target.read_bytes()
        except FileNotFoundError as exc:
            raise ModelRetry(f"File not found: {path}") from exc
        except OSError as exc:
            raise ModelRetry(f"Could not read {path}: {exc}") from exc
        if len(data) > MAX_READ_BYTES:
            raise ModelRetry(
                f"{path} is too large to read in full ({len(data)} bytes); "
                + "inspect it with run_command (e.g. grep/sed) instead."
            )
        return data.decode("utf-8", errors="replace")

    def write_file(self, ctx: RunContext[AgentDeps], path: str, content: str) -> str:
        """Overwrite (or create) a workspace file with ``content``; returns a summary."""

        target = _resolve_in_workspace(ctx.deps, path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ModelRetry(f"Could not write {path}: {exc}") from exc
        return f"Wrote {len(content)} characters to {path}."

    def edit_file(
        self, ctx: RunContext[AgentDeps], path: str, old: str, new: str
    ) -> str:
        """Replace the unique occurrence of ``old`` with ``new`` in a workspace file."""

        target = _resolve_in_workspace(ctx.deps, path)
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ModelRetry(f"File not found: {path}") from exc
        except OSError as exc:
            raise ModelRetry(f"Could not read {path}: {exc}") from exc

        occurrences = text.count(old)
        if occurrences == 0:
            raise ModelRetry(
                f"The text to replace was not found in {path}; re-read the file and "
                + "match it exactly."
            )
        if occurrences > 1:
            raise ModelRetry(
                f"The text to replace appears {occurrences} times in {path}; include "
                + "more surrounding context so it matches exactly once."
            )
        try:
            _ = target.write_text(text.replace(old, new, 1), encoding="utf-8")
        except OSError as exc:
            raise ModelRetry(f"Could not write {path}: {exc}") from exc
        return f"Edited {path}."
