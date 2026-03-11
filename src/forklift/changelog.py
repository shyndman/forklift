from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
import time
from typing import cast, override

from clypi import Command, arg
from pydantic_ai.usage import RunUsage
import structlog
from structlog.stdlib import BoundLogger

from .changelog_analysis import (
    ChangelogAnalysisError,
    build_evidence_bundle,
    build_upstream_narrative_evidence,
)
from .changelog_llm import (
    ChangelogLlmError,
    generate_conflict_review,
    generate_upstream_narrative,
)
from .changelog_models import ChangelogReportSections
from .changelog_renderer import render_changelog_markdown, render_changelog_terminal
from .cli_runtime import apply_cli_overrides, resolved_main_branch
from .opencode_env import (
    DEFAULT_ENV_PATH,
    OpenCodeEnv,
    OpenCodeEnvError,
    load_opencode_env,
)
from .post_run_metrics import UsageSummary, UsageTotals, render_usage_summary

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))
FORK_CONTEXT_FILENAME = "FORK.md"


def _usage_detail(usage: RunUsage, *keys: str) -> int:
    """Read provider-specific usage detail counters with a deterministic fallback."""

    for key in keys:
        value = usage.details.get(key)
        if isinstance(value, int):
            return value
    return 0


def build_changelog_usage_summary(
    usage: RunUsage,
    wall_clock_ms: int,
    *,
    estimated_cost: Decimal | float | None = None,
) -> UsageSummary:
    """Convert pydantic-ai usage into the shared terminal post-run summary shape."""

    totals = UsageTotals(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=_usage_detail(
            usage,
            "reasoning_tokens",
            "reasoning",
            "thoughts_tokens",
        ),
        cache_read_tokens=usage.cache_read_tokens,
        total_tokens=usage.total_tokens,
        total_cost=estimated_cost,
        wall_clock_ms=max(wall_clock_ms, 0),
        tool_calls=usage.tool_calls,
        tool_breakdown=(),
    )
    return UsageSummary.from_totals(totals)


def combine_run_usages(usages: list[RunUsage]) -> RunUsage:
    """Merge per-agent usage records into one command-level usage total."""

    combined_details: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.details.items():
            combined_details[key] = combined_details.get(key, 0) + value

    return RunUsage(
        input_tokens=sum(item.input_tokens for item in usages),
        output_tokens=sum(item.output_tokens for item in usages),
        cache_read_tokens=sum(item.cache_read_tokens for item in usages),
        details=combined_details,
        tool_calls=sum(item.tool_calls for item in usages),
    )


def sum_estimated_costs(costs: list[Decimal | None]) -> Decimal | None:
    """Add available model cost estimates without inventing a value when none exist."""

    present_costs = [cost for cost in costs if cost is not None]
    if not present_costs:
        return None
    return sum(present_costs, start=Decimal("0"))


def _consume_setup_front_matter(front_lines: list[str], start: int) -> int:
    """Advance parser index across setup metadata while validating accepted forms."""

    line = front_lines[start]
    value = line[len("setup:") :].strip()
    if value in ("|", "|-"):
        idx = start + 1
        saw_command_line = False
        while idx < len(front_lines):
            candidate = front_lines[idx]
            if candidate.startswith("  "):
                saw_command_line = True
                idx += 1
                continue
            if candidate.strip() == "":
                idx += 1
                continue
            break
        if not saw_command_line:
            raise ChangelogAnalysisError(
                "FORK.md front matter is malformed: setup block string must include at least one command line."
            )
        return idx

    if value == "":
        raise ChangelogAnalysisError(
            "FORK.md front matter is malformed: setup must be a non-empty string or block string."
        )
    return start + 1


def _consume_changelog_front_matter(
    front_lines: list[str],
    start: int,
) -> tuple[list[str], int]:
    """Parse changelog front matter and return ordered exclusion patterns."""

    line = front_lines[start]
    if line[len("changelog:") :].strip() != "":
        raise ChangelogAnalysisError(
            "FORK.md front matter is malformed: changelog must be an object with nested keys."
        )

    idx = start + 1
    nested_lines: list[str] = []
    while idx < len(front_lines):
        nested = front_lines[idx]
        if nested.startswith("  ") or nested.strip() == "":
            nested_lines.append(nested)
            idx += 1
            continue
        break

    if not nested_lines:
        raise ChangelogAnalysisError(
            "FORK.md front matter is malformed: changelog must define nested metadata with changelog.exclude."
        )

    local_idx = 0
    found_exclude = False
    parsed_excludes: list[str] = []
    while local_idx < len(nested_lines):
        nested_line = nested_lines[local_idx]
        stripped = nested_line.strip()
        if stripped == "" or stripped.startswith("#"):
            local_idx += 1
            continue

        if not nested_line.startswith("  "):
            raise ChangelogAnalysisError(
                "FORK.md front matter is malformed: changelog metadata must be indented by two spaces."
            )

        key_line = nested_line[2:]
        if not key_line.startswith("exclude:"):
            raise ChangelogAnalysisError(
                f"FORK.md front matter is malformed: unsupported changelog key line '{key_line}'. Only 'exclude' is allowed."
            )
        if found_exclude:
            raise ChangelogAnalysisError(
                "FORK.md front matter is malformed: duplicate changelog.exclude key."
            )

        trailing = key_line[len("exclude:") :].strip()
        if trailing:
            raise ChangelogAnalysisError(
                "FORK.md front matter is malformed: changelog.exclude must be a list with one '- pattern' per line."
            )

        found_exclude = True
        local_idx += 1
        while local_idx < len(nested_lines):
            candidate = nested_lines[local_idx]
            if candidate.strip() == "":
                local_idx += 1
                continue
            if candidate.lstrip().startswith("#"):
                local_idx += 1
                continue
            if not candidate.startswith("    "):
                break

            item = candidate[4:]
            if not item.startswith("-"):
                raise ChangelogAnalysisError(
                    "FORK.md front matter is malformed: changelog.exclude entries must be list items prefixed with '-'."
                )
            pattern = item[1:].strip()
            if not pattern:
                raise ChangelogAnalysisError(
                    "FORK.md front matter is malformed: changelog.exclude entries must be non-empty strings."
                )
            parsed_excludes.append(pattern)
            local_idx += 1

    if not found_exclude:
        raise ChangelogAnalysisError(
            "FORK.md front matter is malformed: changelog must include an 'exclude' key."
        )
    if not parsed_excludes:
        raise ChangelogAnalysisError(
            "FORK.md front matter is malformed: changelog.exclude must contain at least one pattern."
        )

    return parsed_excludes, idx


def load_changelog_exclude_patterns(repo_path: Path) -> list[str]:
    """Load repo-owned changelog exclusion rules from strict FORK.md front matter."""

    fork_path = repo_path / FORK_CONTEXT_FILENAME
    if not fork_path.exists():
        return []

    try:
        content = fork_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ChangelogAnalysisError(
            f"Unable to read FORK.md for changelog metadata: {exc}"
        ) from exc

    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return []

    closing = None
    for idx, line in enumerate(lines[1:], start=1):
        if line == "---":
            closing = idx
            break
    if closing is None:
        raise ChangelogAnalysisError(
            "FORK.md front matter is malformed: missing closing '---' delimiter."
        )

    front_lines = lines[1:closing]
    parsed_excludes: list[str] | None = None
    setup_seen = False
    changelog_seen = False

    idx = 0
    while idx < len(front_lines):
        line = front_lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue

        if line.startswith("setup:"):
            if setup_seen:
                raise ChangelogAnalysisError(
                    "FORK.md front matter is malformed: duplicate 'setup' key."
                )
            setup_seen = True
            idx = _consume_setup_front_matter(front_lines, idx)
            continue

        if line.startswith("changelog:"):
            if changelog_seen:
                raise ChangelogAnalysisError(
                    "FORK.md front matter is malformed: duplicate 'changelog' key."
                )
            changelog_seen = True
            parsed_excludes, idx = _consume_changelog_front_matter(front_lines, idx)
            continue

        if line.startswith("  "):
            raise ChangelogAnalysisError(
                f"FORK.md front matter is malformed: unexpected indentation for line '{line}'."
            )
        raise ChangelogAnalysisError(
            f"FORK.md front matter is malformed: unsupported key line '{line}'. Only 'setup' and 'changelog' are allowed."
        )

    return parsed_excludes or []


class Changelog(Command):
    """Generate an operator-facing branch changelog without mutating repository history."""

    repo: Path | str | None = None
    main_branch: str = arg(
        "main",
        help="Name of the primary branch to compare against upstream (default: main)",
    )
    model: str | None = arg(
        None, help="Override OPENCODE_MODEL (letters, numbers, punctuation ._-/)."
    )
    variant: str | None = arg(
        None, help="Override OPENCODE_VARIANT (letters, numbers, punctuation ._-/)."
    )
    agent: str | None = arg(
        None, help="Override OPENCODE_AGENT (letters, numbers, punctuation ._-/)."
    )

    @override
    async def run(self) -> None:
        """<intent>
        Generate a read-only changelog between <main_branch> and upstream/<main_branch> by combining deterministic git evidence (including merge-tree conflict hotspots) with an LLM narrative, without running container orchestration or mutating local history.
        </intent>"""

        repo_path = self._resolve_repo_path()
        branch = resolved_main_branch(self.main_branch)
        env = self._prepare_opencode_env()
        started_at = time.perf_counter()

        try:
            exclusion_patterns = load_changelog_exclude_patterns(repo_path)
            evidence = build_evidence_bundle(
                repo_path,
                branch,
                exclusion_patterns=exclusion_patterns,
            )
            upstream_evidence = build_upstream_narrative_evidence(evidence)
            upstream_result, conflict_result = await asyncio.gather(
                generate_upstream_narrative(upstream_evidence, env),
                generate_conflict_review(evidence, env),
            )
        except (ChangelogAnalysisError, ChangelogLlmError) as exc:
            logger.error("forklift changelog failed", error=str(exc))
            raise SystemExit(1) from exc

        wall_clock_ms = int((time.perf_counter() - started_at) * 1000)
        combined_usage = combine_run_usages([
            upstream_result.usage,
            conflict_result.usage,
        ])
        usage_summary = build_changelog_usage_summary(
            combined_usage,
            wall_clock_ms,
            estimated_cost=sum_estimated_costs(
                [
                    upstream_result.estimated_cost,
                    conflict_result.estimated_cost,
                ]
            ),
        )
        sections = ChangelogReportSections(
            summary_markdown=upstream_result.sections.summary_markdown,
            key_change_arcs_markdown=upstream_result.sections.key_change_arcs_markdown,
            conflict_pair_evaluations_markdown=(
                conflict_result.sections.conflict_pair_evaluations_markdown
            ),
            risk_and_review_notes_markdown=(
                conflict_result.sections.risk_and_review_notes_markdown
            ),
        )
        markdown = render_changelog_markdown(evidence, sections)
        render_changelog_terminal(markdown)
        render_usage_summary("changelog", usage_summary)

    def _resolve_repo_path(self) -> Path:
        """Resolve repo path using current working directory when not explicitly provided."""

        raw = self.repo
        base = Path.cwd() if raw is None else Path(raw)
        return base.expanduser().resolve()

    def _prepare_opencode_env(self) -> OpenCodeEnv:
        """Load OpenCode credentials and apply optional CLI model-routing overrides."""

        try:
            env = load_opencode_env(DEFAULT_ENV_PATH)
        except OpenCodeEnvError as exc:
            logger.error(
                "Failed to load OpenCode config for changelog command",
                path=str(DEFAULT_ENV_PATH),
                error=str(exc),
            )
            raise SystemExit(1) from exc

        return apply_cli_overrides(
            env,
            model=self.model,
            variant=self.variant,
            agent=self.agent,
        )
