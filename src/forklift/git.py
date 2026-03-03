from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import structlog
from structlog.stdlib import BoundLogger

DEFAULT_REQUIRED_REMOTES: Sequence[str] = ("origin", "upstream")
STABLE_VERSION_TAG_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


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


@dataclass(frozen=True)
class ResolvedUpstreamTarget:
    """Describe the selected upstream commit used for orchestration and auditing."""

    policy: str
    target_ref: str
    target_sha: str
    resolved_tag: str | None


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
        output = _run_git(repo_path, ["fetch", remote.name, "--prune", "--tags"])
        results.append(GitFetchResult(name=remote.name, output=output))
    return results


def list_upstream_tag_commits(
    repo_path: Path,
    *,
    remote_name: str = "upstream",
) -> dict[str, str]:
    """Return upstream tag names mapped to commit SHAs for policy resolution."""

    raw_output = _run_git(repo_path, ["ls-remote", "--tags", remote_name])
    raw_tags: dict[str, str] = {}
    peeled_tags: dict[str, str] = {}
    for line in raw_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", maxsplit=1)
        if len(parts) != 2:
            continue
        sha, full_ref = parts
        if not full_ref.startswith("refs/tags/"):
            continue
        tag_ref = full_ref.removeprefix("refs/tags/")
        if tag_ref.endswith("^{}"):
            peeled_tags[tag_ref[:-3]] = sha
            continue
        raw_tags[tag_ref] = sha

    raw_tags.update(peeled_tags)
    return raw_tags


def resolve_upstream_target(
    repo_path: Path,
    *,
    main_branch: str,
    policy: str,
) -> ResolvedUpstreamTarget:
    """Resolve the upstream target commit for a selected rebase policy."""

    if policy == "tip":
        target_ref = f"upstream/{main_branch}"
        return ResolvedUpstreamTarget(
            policy=policy,
            target_ref=target_ref,
            target_sha=_run_git(repo_path, ["rev-parse", target_ref]),
            resolved_tag=None,
        )

    if policy == "latest-version":
        return _resolve_latest_version_target(repo_path)

    raise GitError(f"Unsupported upstream target policy {policy!r}; expected one of: tip, latest-version")


def is_ancestor(repo_path: Path, ancestor_ref: str, descendant_ref: str) -> bool:
    """Return whether `ancestor_ref` is reachable from `descendant_ref`."""

    cmd = ["git", "merge-base", "--is-ancestor", ancestor_ref, descendant_ref]
    logger.debug("Running command", command=" ".join(cmd))
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        cmd,
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    output = (cast(str | None, completed.stdout) or "").strip()
    raise GitError(
        f"git merge-base --is-ancestor {ancestor_ref} {descendant_ref} failed with output:\n{output}"
    )


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


def _resolve_latest_version_target(repo_path: Path) -> ResolvedUpstreamTarget:
    """Select the latest stable upstream version tag and resolve its commit SHA."""

    version_groups: dict[tuple[int, int, int], list[tuple[str, str]]] = {}
    for tag_name, tag_sha in list_upstream_tag_commits(repo_path).items():
        version_key = _stable_version_key(tag_name)
        if version_key is None:
            continue
        version_groups.setdefault(version_key, []).append((tag_name, tag_sha))

    if not version_groups:
        raise GitError(
            "No upstream version tags found for policy latest-version. Expected at least one tag matching X.Y.Z or vX.Y.Z."
        )

    for version_key, entries in version_groups.items():
        prefixed_shas = {sha for tag, sha in entries if tag.startswith("v")}
        bare_shas = {sha for tag, sha in entries if not tag.startswith("v")}
        if prefixed_shas and bare_shas and prefixed_shas != bare_shas:
            formatted_version = ".".join(str(part) for part in version_key)
            details = ", ".join(f"{tag}={sha[:12]}" for tag, sha in sorted(entries))
            raise GitError(
                f"Ambiguous version tags for {formatted_version}: {details}. v-prefixed and unprefixed tags must resolve to the same commit."
            )

    selected_version = max(version_groups)
    selected_entries = version_groups[selected_version]
    selected_shas = {sha for _tag, sha in selected_entries}
    if len(selected_shas) != 1:
        details = ", ".join(f"{tag}={sha[:12]}" for tag, sha in sorted(selected_entries))
        formatted_version = ".".join(str(part) for part in selected_version)
        raise GitError(
            f"Ambiguous version tags for {formatted_version}: {details}. Latest-version policy requires a single target commit."
        )
    selected_sha = next(iter(selected_shas))
    selected_tag = sorted(
        [tag for tag, _sha in selected_entries],
        key=lambda tag: (0 if tag.startswith("v") else 1, tag),
    )[0]
    return ResolvedUpstreamTarget(
        policy="latest-version",
        target_ref=selected_tag,
        target_sha=selected_sha,
        resolved_tag=selected_tag,
    )


def _stable_version_key(tag_name: str) -> tuple[int, int, int] | None:
    """Return parsed `(major, minor, patch)` for stable tags, otherwise `None`."""

    match = STABLE_VERSION_TAG_PATTERN.fullmatch(tag_name)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch))


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
