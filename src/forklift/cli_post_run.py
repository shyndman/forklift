from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, cast

import structlog
from structlog.stdlib import BoundLogger

from .cli_authorship import RewriteResult
from .git import GitError, current_branch, ensure_upstream_merged
from .run_manager import RunPaths

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))

STUCK_EXIT_CODE = 4


def post_container_results(
    repo_path: Path,
    run_paths: RunPaths,
    configured_branch: str,
    *,
    rewrite_and_publish_local_fn: Callable[
        [Path, RunPaths, dict[str, object], str, str], RewriteResult | None
    ],
    log_rewrite_summary_fn: Callable[[Path, RewriteResult | None], None],
    current_branch_fn: Callable[[Path], str] = current_branch,
    ensure_upstream_merged_fn: Callable[[Path, str, str], None] = ensure_upstream_merged,
) -> None:
    """Run post-container verification, rewrite/publication, and summary logging."""

    metadata = load_run_metadata(run_paths.run_dir)
    workspace = run_paths.workspace
    fail_if_stuck(workspace)

    metadata_branch = cast(str | None, metadata.get("main_branch"))
    target_branch = metadata_branch or configured_branch or current_branch_fn(workspace)
    upstream_ref_branch = metadata_branch or configured_branch
    upstream_ref = f"upstream/{upstream_ref_branch}"
    upstream_sha = cast(str | None, metadata.get("target_sha")) or cast(
        str | None, metadata.get("upstream_main_sha")
    )

    try:
        ensure_upstream_merged_fn(workspace, upstream_ref, target_branch)
        if upstream_sha:
            logger.info(
                "Verified %s (%s) is ancestor of %s",
                upstream_ref,
                upstream_sha[:12],
                target_branch,
            )
        else:
            logger.info("Verified %s is ancestor of %s", upstream_ref, target_branch)
    except GitError as exc:
        logger.exception("Upstream verification failed: %s", exc)
        raise SystemExit(3) from exc

    rewrite_result = rewrite_and_publish_local_fn(
        repo_path,
        run_paths,
        metadata,
        target_branch,
        upstream_ref,
    )
    log_rewrite_summary_fn(repo_path, rewrite_result)


def fail_if_stuck(workspace: Path) -> None:
    """Abort post-run verification when the workspace produced `STUCK.md`."""

    stuck_file = workspace / "STUCK.md"
    if not stuck_file.exists():
        return

    logger.warning(
        "STUCK.md detected at %s; skipping verification and local publication.",
        stuck_file,
    )
    raise SystemExit(STUCK_EXIT_CODE)


def load_run_metadata(run_dir: Path) -> dict[str, object]:
    """Load run metadata from disk, returning an empty payload when missing."""

    metadata_path = run_dir / "metadata.json"
    try:
        raw = metadata_path.read_text()
    except FileNotFoundError:
        logger.warning("Metadata file missing", path=metadata_path)
        return {}
    data = cast(dict[str, object], json.loads(raw))
    return data
