from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown

from .changelog_models import ChangelogReportSections, EvidenceBundle

HOTSPOT_CAVEAT = (
    "Tip-merge hotspot predictions are directional and may repeat during "
    "later commit-by-commit rebase picks."
)
MAX_MARKDOWN_WIDTH = 110


def render_changelog_markdown(
    evidence: EvidenceBundle,
    sections: ChangelogReportSections,
) -> str:
    """Assemble fixed-order changelog markdown from section-owned report bodies."""

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
        "## Summary",
        sections.summary_markdown.strip(),
        "",
        "## Key Change Arcs",
        sections.key_change_arcs_markdown.strip(),
        "",
        "## Conflict Pair Evaluations",
        sections.conflict_pair_evaluations_markdown.strip(),
        "",
        "## Risk and Review Notes",
        sections.risk_and_review_notes_markdown.strip(),
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
