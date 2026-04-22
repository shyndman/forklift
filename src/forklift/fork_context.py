from __future__ import annotations

from pathlib import Path

FORK_CONTEXT_CANDIDATES: tuple[Path, ...] = (
    Path("FORK.md"),
    Path(".agents") / "FORK.md",
)


def resolve_fork_context_path(repo_path: Path) -> Path | None:
    """Resolve the repo-owned fork context file using the canonical search order."""

    for relative_path in FORK_CONTEXT_CANDIDATES:
        candidate = repo_path / relative_path
        if candidate.is_file():
            return candidate
    return None
