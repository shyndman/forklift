from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConflictHotspot:
    """Describe a file repeatedly reported in merge-tree conflict metadata."""

    path: str
    conflict_count: int = 1


@dataclass(frozen=True)
class DiffSummary:
    """Summarize deterministic diff totals between analyzed branch refs."""

    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class ChangedFileStat:
    """Capture one changed file row with churn totals and git name-status code."""

    path: str
    added: int = 0
    removed: int = 0
    status: str = "M"


@dataclass(frozen=True)
class EvidenceBundle:
    """Bundle bounded deterministic evidence forwarded to changelog rendering layers."""

    base_sha: str
    main_branch: str
    upstream_ref: str
    conflicts: list[ConflictHotspot] = field(default_factory=list)
    diff_summary: DiffSummary = field(default_factory=DiffSummary)
    top_changed_files: list[ChangedFileStat] = field(default_factory=list)
    important_notes: list[str] = field(default_factory=list)
