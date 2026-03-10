from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown

from .changelog_models import ConflictSideEvidence, EvidenceBundle, TruncationMetadata

HOTSPOT_CAVEAT = (
    "Tip-merge hotspot predictions are directional and may repeat during "
    "later commit-by-commit rebase picks."
)
MAX_MARKDOWN_WIDTH = 110


def _render_truncation_notice(
    label: str,
    metadata: TruncationMetadata | None,
) -> list[str]:
    """Render cap metadata so operators see when extra evidence was trimmed."""

    if metadata is None:
        return []
    counts = f"{metadata.shown}/{metadata.total} (cap {metadata.cap})"
    return [
        f"- {label} truncation: {counts}",
        "- Warning: additional evidence exists beyond configured limits.",
    ]


def _render_conflict_side_summary(
    side_label: str,
    side: ConflictSideEvidence,
) -> list[str]:
    """Render one side's deterministic samples and churn for a conflict path."""

    lines = [
        f"#### {side_label} Side",
        f"- Churn: +{side.insertions} / -{side.deletions}",
        "- Commit samples:",
    ]
    if side.commit_samples:
        for sample in side.commit_samples:
            subject = sample.subject or "(no subject)"
            lines.append(f"  - `{sample.short_sha}` {subject}")
    else:
        lines.append("  - (none)")

    lines.append("- Hunk headers:")
    if side.hunk_headers:
        for header in side.hunk_headers:
            lines.append(f"  - `{header}`")
    else:
        lines.append("  - (none)")

    lines.extend(
        _render_truncation_notice("Commit samples", side.commit_samples_truncation)
    )
    lines.extend(_render_truncation_notice("Hunk headers", side.hunk_headers_truncation))
    return lines


def render_changelog_markdown(evidence: EvidenceBundle, narrative: str) -> str:
    """Assemble fixed-order changelog markdown from evidence and narrative text."""

    baseline = evidence.baseline_diff_summary
    filtered = evidence.filtered_diff_summary

    def _delta(filtered_value: int, baseline_value: int) -> int:
        """Compute filtered-minus-baseline deltas for transparency reporting."""

        return filtered_value - baseline_value

    lines: list[str] = [
        "# Forklift Changelog",
        "",
        "## Branch Context",
        f"- Main branch: `{evidence.main_branch}`",
        f"- Upstream ref: `{evidence.upstream_ref}`",
        f"- Merge base: `{evidence.base_sha[:12]}`",
        "",
        narrative.strip(),
        "",
        "## Predicted Conflict Hotspots",
    ]

    if evidence.conflicts:
        lines.append("| Path | Conflict Count |")
        lines.append("| --- | ---: |")
        for hotspot in evidence.conflicts:
            lines.append(f"| `{hotspot.path}` | {hotspot.conflict_count} |")
    else:
        lines.append("- No hotspot paths detected for the analyzed branch tips.")

    if evidence.conflict_side_comparisons:
        lines.append("")
        lines.append("## Conflict Side Comparisons")
        ordered_comparisons = sorted(
            evidence.conflict_side_comparisons,
            key=lambda item: (-item.conflict_count, item.path),
        )
        for comparison in ordered_comparisons:
            lines.extend(
                [
                    "",
                    f"### `{comparison.path}` (conflict count: {comparison.conflict_count})",
                    *_render_conflict_side_summary("Fork", comparison.fork_side),
                    *_render_conflict_side_summary("Upstream", comparison.upstream_side),
                ]
            )

    lines.extend(
        [
            "",
            f"- Caveat: {HOTSPOT_CAVEAT}",
            "",
            "## Deterministic Supporting Metrics",
            "| Metric | All Files | Excluding Patterns | Delta |",
            "| --- | ---: | ---: | ---: |",
            (
                "| Files changed | "
                f"{baseline.files_changed} | "
                f"{filtered.files_changed} | "
                f"{_delta(filtered.files_changed, baseline.files_changed)} |"
            ),
            (
                "| Insertions | "
                f"{baseline.insertions} | "
                f"{filtered.insertions} | "
                f"{_delta(filtered.insertions, baseline.insertions)} |"
            ),
            (
                "| Deletions | "
                f"{baseline.deletions} | "
                f"{filtered.deletions} | "
                f"{_delta(filtered.deletions, baseline.deletions)} |"
            ),
            "",
            "### Top Changed Files",
        ]
    )

    if evidence.top_changed_files:
        lines.append("| Path | Status | Added | Removed |")
        lines.append("| --- | :---: | ---: | ---: |")
        for item in evidence.top_changed_files:
            lines.append(
                f"| `{item.path}` | {item.status} | {item.added} | {item.removed} |"
            )
    else:
        lines.append("- No changed files were reported by deterministic diff analysis.")

    if evidence.important_notes:
        lines.append("")
        lines.append("### Important Notes")
        for note in evidence.important_notes:
            lines.append(f"- {note}")

    lines.append("")
    lines.append("### Exclusion Rules")
    if evidence.active_exclusion_rules:
        for pattern in evidence.active_exclusion_rules:
            lines.append(f"- `{pattern}`")
        lines.append(
            f"- Matched files in baseline diff: {evidence.excluded_file_count}"
        )
    else:
        lines.append("- No exclusion rules configured.")

    return "\n".join(lines).strip() + "\n"


def render_changelog_terminal(markdown: str, *, console: Console | None = None) -> None:
    """Render changelog markdown in terminal output using Rich markdown APIs."""

    active_console = console or Console()
    active_console.print(Markdown(markdown), width=MAX_MARKDOWN_WIDTH)
