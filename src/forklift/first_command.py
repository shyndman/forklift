from __future__ import annotations

from pathlib import Path
from typing import override

from clypi import Command, arg

from .changelog_analysis import (
    ChangelogAnalysisError,
    compute_merge_base,
    resolve_analysis_refs,
)
from .git import GitError, run_git


class First(Command):
    """Print the first fork-only commit without mutating repository history."""

    repo: Path | str | None = None
    main_branch: str = arg(
        "main",
        help="Name of the primary branch to compare against upstream (default: main)",
    )

    @override
    async def run(self) -> None:
        repo_path = self._resolve_repo_path()

        try:
            branch, upstream_ref = resolve_analysis_refs(repo_path, self.main_branch)
            base_sha = compute_merge_base(repo_path, branch, upstream_ref)
            print(
                find_first_divergent_commit(
                    repo_path, revision_range=f"{base_sha}..{branch}"
                )
            )
        except ChangelogAnalysisError as exc:
            raise SystemExit(f"first error: {exc}") from exc

    def _resolve_repo_path(self) -> Path:
        """Resolve repo path using current working directory when not explicitly provided."""

        raw = self.repo
        base = Path.cwd() if raw is None else Path(raw)
        return base.expanduser().resolve()


def find_first_divergent_commit(repo_path: Path, *, revision_range: str) -> str:
    """Return the first full SHA reachable in ``revision_range`` or fail if none exist."""

    try:
        output = run_git(
            repo_path,
            ["log", "--format=%H", "--reverse", "--ancestry-path", revision_range],
        )
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to locate first divergent commit for range {revision_range}: {exc}"
        ) from exc

    for line in output.splitlines():
        commit_sha = line.strip()
        if commit_sha:
            return commit_sha

    raise ChangelogAnalysisError("no divergent commits")


__all__ = ["First"]
