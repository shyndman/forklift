from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import structlog
from structlog.stdlib import BoundLogger

DEFAULT_REQUIRED_REMOTES: Sequence[str] = ("origin", "upstream")


logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))


class GitError(RuntimeError):
    """Raised when a Git subprocess fails."""


@dataclass(frozen=True)
class GitRemote:
    name: str
    fetch_url: str


@dataclass(frozen=True)
class GitFetchResult:
    name: str
    output: str


def discover_remotes(repo_path: Path) -> dict[str, GitRemote]:
    """Return all fetch remotes configured for ``repo_path``."""

    raw_output = _run_git(repo_path, ["remote", "-v"])
    remotes: dict[str, GitRemote] = {}

    for line in raw_output.splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2]
        if kind != "(fetch)" or name in remotes:
            continue
        remotes[name] = GitRemote(name=name, fetch_url=url)

    return remotes


def ensure_required_remotes(
    repo_path: Path, required: Sequence[str] = DEFAULT_REQUIRED_REMOTES
) -> dict[str, GitRemote]:
    remotes = discover_remotes(repo_path)
    missing = [name for name in required if name not in remotes]
    if missing:
        raise GitError("Missing required Git remote(s): " + ", ".join(sorted(missing)))
    return remotes


def fetch_remotes(
    repo_path: Path,
    remotes: dict[str, GitRemote],
    names: Iterable[str] | None = None,
) -> list[GitFetchResult]:
    targets = list(names) if names is not None else list(remotes.keys())
    if not targets:
        raise GitError("No remotes available to fetch")

    results: list[GitFetchResult] = []
    for remote_name in targets:
        remote = remotes.get(remote_name)
        if remote is None:
            raise GitError(f"Remote '{remote_name}' is not configured")
        logger.info("Fetching remote", name=remote.name, fetch_url=remote.fetch_url)
        output = _run_git(repo_path, ["fetch", remote.name, "--prune"])
        results.append(GitFetchResult(name=remote.name, output=output))
    return results


def current_branch(repo_path: Path) -> str:
    return _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])


def create_branch(repo_path: Path, branch: str, start_point: str) -> None:
    _ = _run_git(repo_path, ["checkout", "-B", branch, start_point])


def run_merge(repo_path: Path, upstream_ref: str) -> None:
    _ = _run_git(repo_path, ["merge", upstream_ref])


def has_unpushed_changes(repo_path: Path) -> bool:
    status = _run_git(repo_path, ["status", "-sb"])
    return "ahead" in status


def ensure_upstream_merged(repo_path: Path, upstream_ref: str, branch: str) -> None:
    _ = _run_git(repo_path, ["merge-base", "--is-ancestor", upstream_ref, branch])


def run_git(repo_path: Path, args: Sequence[str]) -> str:
    return _run_git(repo_path, args)


def _run_git(repo_path: Path, args: Sequence[str]) -> str:
    cmd = ["git", *args]
    logger.debug("Running command", command=" ".join(cmd))
    try:
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            cmd,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        output = (cast(str | None, exc.stdout) or "").strip()
        raise GitError(f"git {' '.join(args)} failed with output:\n{output}") from exc
    stdout = cast(str | None, completed.stdout)
    return (stdout or "").strip()
