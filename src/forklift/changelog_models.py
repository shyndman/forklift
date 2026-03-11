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
class TruncationMetadata:
    """Describe how much evidence is shown when collection caps are reached."""

    shown: int
    total: int
    cap: int


@dataclass(frozen=True)
class CommitSample:
    """Represent one path-scoped commit sample for side-intent evaluation."""

    short_sha: str
    subject: str


@dataclass(frozen=True)
class ConflictSideEvidence:
    """Capture deterministic evidence for one side of a single conflict path."""

    commit_samples: list[CommitSample] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    hunk_headers: list[str] = field(default_factory=list)
    commit_samples_truncation: TruncationMetadata | None = None
    hunk_headers_truncation: TruncationMetadata | None = None


@dataclass(frozen=True)
class ConflictSideComparison:
    """Pair fork/upstream evidence for one conflict path in mechanical order."""

    path: str
    conflict_count: int
    fork_side: ConflictSideEvidence = field(default_factory=ConflictSideEvidence)
    upstream_side: ConflictSideEvidence = field(default_factory=ConflictSideEvidence)


@dataclass(frozen=True)
class UpstreamNarrativeEvidence:
    """Carry only upstream-oriented evidence for the top-half changelog narrative."""

    base_sha: str
    main_branch: str
    upstream_ref: str
    baseline_diff_summary: DiffSummary = field(default_factory=DiffSummary)
    filtered_diff_summary: DiffSummary = field(default_factory=DiffSummary)
    active_exclusion_rules: list[str] = field(default_factory=list)
    excluded_file_count: int = 0
    diff_summary: DiffSummary = field(default_factory=DiffSummary)
    top_changed_files: list[ChangedFileStat] = field(default_factory=list)
    important_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UpstreamNarrativeSections:
    """Hold the top-half report section bodies produced from upstream-only evidence."""

    summary_markdown: str
    key_change_arcs_markdown: str


@dataclass(frozen=True)
class ConflictReviewSections:
    """Hold the bottom-half report section bodies produced from full conflict evidence."""

    conflict_pair_evaluations_markdown: str
    risk_and_review_notes_markdown: str


@dataclass(frozen=True)
class ChangelogReportSections:
    """Represent the full changelog narrative after host-side section ownership is enforced."""

    summary_markdown: str
    key_change_arcs_markdown: str
    conflict_pair_evaluations_markdown: str
    risk_and_review_notes_markdown: str


@dataclass(frozen=True)
class EvidenceBundle:
    """Bundle deterministic evidence and exclusion metadata used by changelog layers."""

    base_sha: str
    main_branch: str
    upstream_ref: str
    conflicts: list[ConflictHotspot] = field(default_factory=list)
    baseline_diff_summary: DiffSummary = field(default_factory=DiffSummary)
    filtered_diff_summary: DiffSummary = field(default_factory=DiffSummary)
    active_exclusion_rules: list[str] = field(default_factory=list)
    excluded_file_count: int = 0
    diff_summary: DiffSummary = field(default_factory=DiffSummary)
    top_changed_files: list[ChangedFileStat] = field(default_factory=list)
    conflict_side_comparisons: list[ConflictSideComparison] = field(default_factory=list)
    important_notes: list[str] = field(default_factory=list)
