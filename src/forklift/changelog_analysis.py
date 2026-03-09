from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from pathlib import PurePosixPath
from typing import cast

from .changelog_models import (
    ChangedFileStat,
    ConflictHotspot,
    DiffSummary,
    EvidenceBundle,
)
from .cli_runtime import resolved_main_branch
from .git import GitError, ensure_required_remotes, fetch_remotes, run_git

MIN_GIT_VERSION = (2, 38, 0)
DEFAULT_TOP_CHANGED_FILES = 30
IMPORTANT_NOTE_HOTSPOT_PREDICTION = (
    "Conflict hotspots are predicted from a tip merge and may recur during later "
    "commit-by-commit rebase picks."
)
GIT_VERSION_PATTERN = re.compile(
    r"^git version (?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
)
MERGE_TREE_CONFLICT_LINE_PATTERN = re.compile(
    r"^(?P<mode>\d{6})\s+(?P<object>[0-9A-Fa-f]+)\s+(?P<stage>[123])\s+(?P<path>.+)$"
)


class ChangelogAnalysisError(RuntimeError):
    """Raised when deterministic changelog evidence cannot be computed safely."""


@dataclass(frozen=True)
class MergeTreeResult:
    """Capture merge-tree output text plus raw process exit status."""

    exit_code: int
    output: str


def resolve_analysis_refs(repo_path: Path, main_branch: str) -> tuple[str, str]:
    """Resolve and validate branch refs used by deterministic changelog analysis."""

    resolved_main = resolved_main_branch(main_branch)
    upstream_ref = f"upstream/{resolved_main}"
    try:
        _ = run_git(repo_path, ["rev-parse", "--verify", resolved_main])
        _ = run_git(repo_path, ["rev-parse", "--verify", upstream_ref])
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to resolve analysis refs {resolved_main!r} and {upstream_ref!r}: {exc}"
        ) from exc
    return resolved_main, upstream_ref


def ensure_supported_git_version(repo_path: Path) -> tuple[int, int, int]:
    """Require host Git 2.38+ so modern merge-tree conflict metadata is available."""

    try:
        version_output = run_git(repo_path, ["--version"])
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to check host Git version: {exc}"
        ) from exc

    match = GIT_VERSION_PATTERN.match(version_output)
    if match is None:
        raise ChangelogAnalysisError(
            f"Unable to parse host Git version from output: {version_output!r}"
        )

    parsed_version = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )
    if parsed_version < MIN_GIT_VERSION:
        required = ".".join(str(part) for part in MIN_GIT_VERSION)
        current = ".".join(str(part) for part in parsed_version)
        message = (
            f"forklift changelog requires Git {required}+ for modern merge-tree output; "
            f"detected Git {current}. Upgrade Git and rerun the command."
        )
        raise ChangelogAnalysisError(message)

    return parsed_version


def compute_merge_base(repo_path: Path, main_branch: str, upstream_ref: str) -> str:
    """Return the full merge-base SHA between selected local and upstream refs."""

    try:
        return run_git(repo_path, ["merge-base", main_branch, upstream_ref])
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to compute merge-base for {main_branch} and {upstream_ref}: {exc}"
        ) from exc


def run_merge_tree(
    repo_path: Path, main_branch: str, upstream_ref: str
) -> MergeTreeResult:
    """Run merge-tree in write-tree mode while preserving stdout and exit code."""

    cmd = ["git", "merge-tree", "--write-tree", main_branch, upstream_ref]
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        cmd,
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    output = (cast(str | None, completed.stdout) or "").strip()
    return MergeTreeResult(exit_code=completed.returncode, output=output)


def parse_merge_tree_conflict_hotspots(merge_tree_output: str) -> list[ConflictHotspot]:
    """Parse merge-tree conflicted file tuples into per-path hotspot counts."""

    stage_counts: dict[str, dict[str, int]] = {}
    for line in merge_tree_output.splitlines():
        match = MERGE_TREE_CONFLICT_LINE_PATTERN.match(line.strip())
        if match is None:
            continue
        path = match.group("path")
        stage = match.group("stage")
        per_path = stage_counts.setdefault(path, {"1": 0, "2": 0, "3": 0})
        per_path[stage] += 1

    hotspots: list[ConflictHotspot] = []
    for path, counts in stage_counts.items():
        conflict_count = max(counts.values())
        hotspots.append(ConflictHotspot(path=path, conflict_count=conflict_count))

    hotspots.sort(key=lambda item: (-item.conflict_count, item.path))
    return hotspots


def resolve_merge_tree_hotspots(result: MergeTreeResult) -> list[ConflictHotspot]:
    """Interpret merge-tree exit semantics and return deterministic conflict hotspots."""

    if result.exit_code == 0:
        return []
    if result.exit_code == 1:
        hotspots = parse_merge_tree_conflict_hotspots(result.output)
        if not hotspots:
            raise ChangelogAnalysisError(
                "merge-tree reported conflicts but no conflicted file metadata could be parsed"
            )
        return hotspots
    raise ChangelogAnalysisError(
        f"git merge-tree failed with exit status {result.exit_code}."
    )


def canonicalize_changed_path(raw_path: str) -> str:
    """Normalize git diff paths so rename/copy rows use destination-path semantics."""

    path = raw_path.strip()
    if " => " not in path:
        return path

    if "{" in path and "}" in path:
        prefix, remainder = path.split("{", maxsplit=1)
        middle, suffix = remainder.split("}", maxsplit=1)
        if " => " in middle:
            _old_name, new_name = middle.split(" => ", maxsplit=1)
            return f"{prefix}{new_name}{suffix}".strip()

    _old_path, new_path = path.split(" => ", maxsplit=1)
    return new_path.strip()


def path_matches_exclusion_pattern(path: str, pattern: str) -> bool:
    """Apply gitignore-like path matching for one exclusion pattern."""

    normalized_path = path.strip().lstrip("./")
    normalized_pattern = pattern.strip().lstrip("/")
    if not normalized_path or not normalized_pattern:
        return False

    if "/" not in normalized_pattern:
        return any(
            fnmatchcase(segment, normalized_pattern)
            for segment in normalized_path.split("/")
        )

    return PurePosixPath(normalized_path).match(normalized_pattern)


def is_path_excluded(path: str, exclusion_patterns: list[str]) -> bool:
    """Evaluate ordered exclusion rules using last-match-wins semantics."""

    included = True
    for raw_pattern in exclusion_patterns:
        is_negated = raw_pattern.startswith("!")
        pattern = raw_pattern[1:] if is_negated else raw_pattern
        if not pattern:
            continue
        if not path_matches_exclusion_pattern(path, pattern):
            continue
        included = is_negated
    return not included


def filter_changed_file_stats(
    changed_file_stats: list[ChangedFileStat],
    exclusion_patterns: list[str],
) -> tuple[list[ChangedFileStat], int]:
    """Filter changed-file rows using exclusion patterns and return excluded-count metadata."""

    filtered: list[ChangedFileStat] = []
    excluded_count = 0
    for item in changed_file_stats:
        if is_path_excluded(item.path, exclusion_patterns):
            excluded_count += 1
            continue
        filtered.append(item)
    return filtered, excluded_count


def filter_conflict_hotspots(
    hotspots: list[ConflictHotspot],
    exclusion_patterns: list[str],
) -> list[ConflictHotspot]:
    """Apply exclusion rules to merge-tree conflict hotspots for consistent reporting."""

    return [
        hotspot
        for hotspot in hotspots
        if not is_path_excluded(hotspot.path, exclusion_patterns)
    ]


def parse_numstat_output(numstat_output: str) -> dict[str, tuple[int, int]]:
    """Parse git diff --numstat rows into per-file added/removed counts."""

    parsed: dict[str, tuple[int, int]] = {}
    for raw_line in numstat_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = raw_line.split("\t")
        if len(parts) < 3:
            continue
        added_raw = parts[0].strip()
        removed_raw = parts[1].strip()
        path = parts[2].strip() if len(parts) == 3 else parts[-1].strip()
        path = canonicalize_changed_path(path)
        if not path:
            continue

        added = int(added_raw) if added_raw.isdigit() else 0
        removed = int(removed_raw) if removed_raw.isdigit() else 0
        current_added, current_removed = parsed.get(path, (0, 0))
        parsed[path] = (current_added + added, current_removed + removed)
    return parsed


def parse_name_status_output(name_status_output: str) -> dict[str, str]:
    """Parse git diff --name-status rows into per-file status codes."""

    parsed: dict[str, str] = {}
    for raw_line in name_status_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = raw_line.split("\t")
        if len(parts) < 2:
            continue
        status_field = parts[0].strip()
        if not status_field:
            continue

        status = status_field[0]
        if status in {"R", "C"} and len(parts) >= 3:
            path = parts[2].strip()
        else:
            path = parts[1].strip()
        path = canonicalize_changed_path(path)
        if not path:
            continue
        parsed[path] = status
    return parsed


def build_changed_file_stats(
    numstat: dict[str, tuple[int, int]],
    name_status: dict[str, str],
) -> list[ChangedFileStat]:
    """Combine numstat and name-status maps into deterministic changed-file rows."""

    paths = sorted(set(numstat) | set(name_status))
    stats: list[ChangedFileStat] = []
    for path in paths:
        added, removed = numstat.get(path, (0, 0))
        status = name_status.get(path, "M")
        stats.append(
            ChangedFileStat(
                path=path,
                added=added,
                removed=removed,
                status=status,
            )
        )

    stats.sort(key=lambda item: (-(item.added + item.removed), item.path))
    return stats


def build_diff_summary(changed_file_stats: list[ChangedFileStat]) -> DiffSummary:
    """Aggregate changed-file rows into stable top-line diff summary counts."""

    return DiffSummary(
        files_changed=len(changed_file_stats),
        insertions=sum(item.added for item in changed_file_stats),
        deletions=sum(item.removed for item in changed_file_stats),
    )


def collect_supporting_diff_stats(
    repo_path: Path,
    main_branch: str,
    upstream_ref: str,
) -> tuple[DiffSummary, list[ChangedFileStat]]:
    """Collect deterministic numstat/name-status outputs for the branch comparison range."""

    comparison_range = f"{main_branch}...{upstream_ref}"
    try:
        numstat_output = run_git(repo_path, ["diff", "--numstat", comparison_range])
        name_status_output = run_git(
            repo_path,
            ["diff", "--name-status", comparison_range],
        )
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to collect diff statistics for range {comparison_range}: {exc}"
        ) from exc

    changed_file_stats = build_changed_file_stats(
        parse_numstat_output(numstat_output),
        parse_name_status_output(name_status_output),
    )
    return build_diff_summary(changed_file_stats), changed_file_stats


def build_evidence_bundle(
    repo_path: Path,
    main_branch: str,
    *,
    exclusion_patterns: list[str] | None = None,
    max_changed_files: int = DEFAULT_TOP_CHANGED_FILES,
) -> EvidenceBundle:
    """Compute deterministic changelog evidence with optional exclusion-aware filtering."""

    _ = ensure_supported_git_version(repo_path)

    try:
        remotes = ensure_required_remotes(repo_path)
        _ = fetch_remotes(repo_path, remotes)
    except GitError as exc:
        raise ChangelogAnalysisError(
            f"Unable to refresh required remotes: {exc}"
        ) from exc

    resolved_main, upstream_ref = resolve_analysis_refs(repo_path, main_branch)
    base_sha = compute_merge_base(repo_path, resolved_main, upstream_ref)
    hotspots = resolve_merge_tree_hotspots(
        run_merge_tree(repo_path, resolved_main, upstream_ref)
    )
    baseline_diff_summary, changed_file_stats = collect_supporting_diff_stats(
        repo_path,
        resolved_main,
        upstream_ref,
    )
    active_exclusion_rules = [
        pattern.strip() for pattern in (exclusion_patterns or []) if pattern.strip()
    ]
    filtered_changed_file_stats, excluded_file_count = filter_changed_file_stats(
        changed_file_stats,
        active_exclusion_rules,
    )
    filtered_hotspots = filter_conflict_hotspots(hotspots, active_exclusion_rules)
    filtered_diff_summary = build_diff_summary(filtered_changed_file_stats)

    bounded_limit = max(0, max_changed_files)
    return EvidenceBundle(
        base_sha=base_sha,
        main_branch=resolved_main,
        upstream_ref=upstream_ref,
        conflicts=filtered_hotspots,
        baseline_diff_summary=baseline_diff_summary,
        filtered_diff_summary=filtered_diff_summary,
        active_exclusion_rules=active_exclusion_rules,
        excluded_file_count=excluded_file_count,
        diff_summary=filtered_diff_summary,
        top_changed_files=filtered_changed_file_stats[:bounded_limit],
        important_notes=[IMPORTANT_NOTE_HOTSPOT_PREDICTION],
    )
