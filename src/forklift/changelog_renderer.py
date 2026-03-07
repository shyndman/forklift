from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown

from .changelog_models import EvidenceBundle

HOTSPOT_CAVEAT = (
    "Tip-merge hotspot predictions are directional and may repeat during "
    "later commit-by-commit rebase picks."
)
MAX_MARKDOWN_WIDTH = 110


def render_changelog_markdown(evidence: EvidenceBundle, narrative: str) -> str:
    """Assemble fixed-order changelog markdown from evidence and narrative text."""

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

    lines.extend(
        [
            "",
            f"- Caveat: {HOTSPOT_CAVEAT}",
            "",
            "## Deterministic Supporting Metrics",
            f"- Files changed: {evidence.diff_summary.files_changed}",
            f"- Insertions: {evidence.diff_summary.insertions}",
            f"- Deletions: {evidence.diff_summary.deletions}",
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

    return "\n".join(lines).strip() + "\n"


def render_changelog_terminal(markdown: str, *, console: Console | None = None) -> None:
    """Render changelog markdown in terminal output using Rich markdown APIs."""

    active_console = console or Console()
    active_console.print(Markdown(markdown), width=MAX_MARKDOWN_WIDTH)
