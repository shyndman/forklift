from __future__ import annotations

from pathlib import Path
from typing import override

from clypi import Command, arg

from .changelog_analysis import (
    ChangelogAnalysisError,
    compute_merge_base,
    parse_name_status_entries_output,
    resolve_analysis_refs,
)
from .git import GitError, run_git

OWNED_PATH_STATUSES = frozenset({"A", "R", "C"})


class Files(Command):
    """List paths currently owned by the fork without mutating repository history."""

    repo: Path | str | None = None
    main_branch: str = arg(
        "main",
        help="Name of the primary branch to compare against upstream (default: main)",
    )
    hash: bool = arg(
        False,
        help="Show the short commit where the current path first appeared in merge-base..<main-branch>.",
    )

    @override
    async def run(self) -> None:
        repo_path = self._resolve_repo_path()

        try:
            branch, upstream_ref = resolve_analysis_refs(repo_path, self.main_branch)
            owned_paths = collect_fork_owned_paths(repo_path, branch, upstream_ref)
            if not owned_paths:
                print("No fork-owned files.")
                return

            if not self.hash:
                print("\n".join(owned_paths))
                return

            base_sha = compute_merge_base(repo_path, branch, upstream_ref)
            revision_range = f"{base_sha}..{branch}"
            for path in owned_paths:
                short_sha = find_current_path_introduction_commit(
                    repo_path,
                    revision_range=revision_range,
                    path=path,
                )
                print(f"{path}\t{short_sha}")
        except ChangelogAnalysisError as exc:
            raise SystemExit(f"files error: {exc}") from exc

    def _resolve_repo_path(self) -> Path:
        """Resolve repo path using current working directory when not explicitly provided."""

        raw = self.repo
        base = Path.cwd() if raw is None else Path(raw)
        return base.expanduser().resolve()


def collect_fork_owned_paths(
    repo_path: Path,
    main_branch: str,
    upstream_ref: str,
) -> list[str]:
    """Return alphabetized current paths that exist only on the fork side."""

    comparison_range = f"{upstream_ref}..{main_branch}"
    try:
        name_status_output = run_git(
            repo_path,
            [
                "diff",
                "--name-status",
                "--find-renames",
                "--find-copies",
                comparison_range,
            ],
        )
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to collect fork-owned paths for range {comparison_range}: {exc}"
        ) from exc

    return sorted(
        {
            item.path
            for item in parse_name_status_entries_output(name_status_output)
            if item.status in OWNED_PATH_STATUSES
        }
    )


def find_current_path_introduction_commit(
    repo_path: Path,
    *,
    revision_range: str,
    path: str,
) -> str:
    """Return the first short SHA where the current path appears in the fork range."""

    try:
        output = run_git(
            repo_path,
            ["log", "--format=%h", "--reverse", revision_range, "--", path],
        )
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to locate introduction commit for {path!r} in range {revision_range}: {exc}"
        ) from exc

    for line in output.splitlines():
        short_sha = line.strip()
        if short_sha:
            return short_sha

    raise ChangelogAnalysisError(
        f"Unable to locate introduction commit for {path!r} in range {revision_range}."
    )


__all__ = ["Files"]
