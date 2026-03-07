from __future__ import annotations

from pathlib import Path
from typing import cast, override

from clypi import Command, arg
import structlog
from structlog.stdlib import BoundLogger

from .changelog_analysis import ChangelogAnalysisError, build_evidence_bundle
from .changelog_llm import ChangelogLlmError, generate_changelog_narrative
from .changelog_renderer import render_changelog_markdown, render_changelog_terminal
from .cli_runtime import apply_cli_overrides, resolved_main_branch
from .opencode_env import (
    DEFAULT_ENV_PATH,
    OpenCodeEnv,
    OpenCodeEnvError,
    load_opencode_env,
)

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))


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

        try:
            evidence = build_evidence_bundle(repo_path, branch)
            narrative = generate_changelog_narrative(evidence, env)
        except (ChangelogAnalysisError, ChangelogLlmError) as exc:
            logger.error("forklift changelog failed", error=str(exc))
            raise SystemExit(1) from exc

        markdown = render_changelog_markdown(evidence, narrative)
        render_changelog_terminal(markdown)

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
