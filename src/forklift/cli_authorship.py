from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, cast

import structlog
from structlog.stdlib import BoundLogger

from .git import GitError, current_branch, ensure_upstream_merged, run_git
from .run_manager import RunPaths

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))

AGENT_NAME = "Forklift Agent"
AGENT_EMAIL = "forklift@github.com"
STASH_MESSAGE = "forklift-authorship-rewrite"
FILTER_REPO_INSTALL_HELP = (
    "Install git filter-repo 2.47.0+: pip install git-filter-repo==2.47.0, "
    "brew install git-filter-repo, or download the standalone script from "
    "https://github.com/newren/git-filter-repo/releases (requires git >= 2.22 and python >= 3.6)."
)


@dataclass(frozen=True)
class OperatorIdentity:
    """Captures the host operator identity used for commit authorship rewrite."""

    name: str
    email: str


@dataclass
class RewriteResult:
    """Summarizes bounded rewrite/local publication outcomes for post-run logging."""

    branch: str
    operator: OperatorIdentity
    rewrite_range: str
    publication_branch: str | None
    stash_created: bool
    stash_conflicts: bool
    rewritten: bool
    published: bool


def workspace_has_changes(workspace: Path) -> bool:
    """Return whether the workspace has uncommitted changes before rewrite."""

    status = run_git(workspace, ["status", "--porcelain"])
    return bool(status.strip())


def ensure_rewrite_anchor_branch(
    workspace: Path,
    target_branch: str,
    upstream_anchor: str,
    *,
    run_git_cmd: Callable[[Path, list[str]], str] = run_git,
) -> str:
    """Ensure a stable local ref exists for bounded rewrite ranges."""

    helper_branch = f"upstream-{target_branch.replace('/', '-')}"
    try:
        helper_sha = run_git_cmd(workspace, ["rev-parse", "--verify", helper_branch])
    except GitError:
        logger.info(
            "Recreating rewrite anchor branch",
            branch=helper_branch,
            upstream_sha=upstream_anchor,
        )
        _ = run_git_cmd(workspace, ["branch", "-f", helper_branch, upstream_anchor])
        return helper_branch
    if helper_sha == upstream_anchor:
        return helper_branch
    logger.info(
        "Resetting rewrite anchor branch",
        branch=helper_branch,
        old_sha=helper_sha[:12],
        new_sha=upstream_anchor[:12],
    )
    _ = run_git_cmd(workspace, ["branch", "-f", helper_branch, upstream_anchor])
    return helper_branch


def build_publication_branch(metadata: dict[str, object], target_branch: str) -> str:
    """Build the local review branch name for rewritten output."""

    raw_timestamp = cast(str | None, metadata.get("created_at"))
    if raw_timestamp:
        try:
            parsed = datetime.strptime(raw_timestamp, "%Y%m%d_%H%M%S")
        except ValueError:
            logger.warning(
                "Unexpected metadata timestamp format; using normalized value.",
                value=raw_timestamp,
            )
            publication_timestamp = raw_timestamp.replace("_", "T")
        else:
            publication_timestamp = parsed.strftime("%Y%m%dT%H%M%S")
    else:
        publication_timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"upstream-merge/{publication_timestamp}/{target_branch}"


def publish_to_local(
    workspace: Path,
    repo_path: Path,
    source_branch: str,
    publication_branch: str,
    *,
    run_git_cmd: Callable[[Path, list[str]], str] = run_git,
) -> None:
    """Publish rewritten workspace commits to a local handoff branch."""

    push_output = run_git_cmd(
        workspace,
        [
            "push",
            str(repo_path),
            f"{source_branch}:{publication_branch}",
            "--force",
        ],
    )
    if push_output:
        logger.info("Local publication output", output=push_output)


def validate_filter_repo(
    workspace: Path,
    *,
    run_git_cmd: Callable[[Path, list[str]], str] = run_git,
) -> None:
    """Ensure `git filter-repo` is available before rewriting commits."""

    try:
        version = run_git_cmd(workspace, ["filter-repo", "--version"]).strip()
    except GitError as exc:
        logger.exception("git filter-repo not available: %s", exc)
        logger.error(FILTER_REPO_INSTALL_HELP)
        raise SystemExit(1) from exc
    if version:
        logger.info("git filter-repo detected", version=version)


def write_mailmap(run_dir: Path, operator: OperatorIdentity) -> Path:
    """Write a temporary mailmap that rewrites Forklift Agent commits to operator identity."""

    mailmap_path = run_dir / "authorship.mailmap"
    mapping = f"{operator.name} <{operator.email}> {AGENT_NAME} <{AGENT_EMAIL}>\n"
    _ = mailmap_path.write_text(mapping)
    return mailmap_path


def assert_no_agent_commits(
    workspace: Path,
    rewrite_range: str,
    *,
    run_git_cmd: Callable[[Path, list[str]], str] = run_git,
) -> None:
    """Fail if rewrite range still contains commits authored by the Forklift agent identity."""

    residual = run_git_cmd(
        workspace,
        [
            "log",
            "--format=%H",
            f"--author={AGENT_NAME} <{AGENT_EMAIL}>",
            rewrite_range,
        ],
    ).strip()
    if residual:
        sample = ", ".join(residual.splitlines()[:5])
        logger.error(
            "Authorship rewrite incomplete in range %s; commits authored by %s <%s> remain: %s",
            rewrite_range,
            AGENT_NAME,
            AGENT_EMAIL,
            sample,
        )
        raise SystemExit(1)


def pop_stash(
    workspace: Path,
    *,
    run_git_cmd: Callable[[Path, list[str]], str] = run_git,
) -> bool:
    """Attempt to restore stashed workspace state after rewrite."""

    try:
        output = run_git_cmd(workspace, ["stash", "pop"])
    except GitError as exc:
        logger.warning(
            "Unable to auto-pop stash '%s': %s. Recover manually via `git stash list`.",
            STASH_MESSAGE,
            exc,
        )
        return False
    if output:
        logger.info("Stash pop output", output=output)
    return True


def rewrite_and_publish_local(
    repo_path: Path,
    run_paths: RunPaths,
    metadata: dict[str, object],
    target_branch: str,
    upstream_ref: str,
    *,
    run_git_cmd: Callable[[Path, list[str]], str] = run_git,
    current_branch_fn: Callable[[Path], str] = current_branch,
    ensure_upstream_merged_fn: Callable[[Path, str, str], None] = ensure_upstream_merged,
    workspace_has_changes_fn: Callable[[Path], bool] = workspace_has_changes,
) -> RewriteResult | None:
    """Rewrite agent authorship in bounded range and publish to local handoff branch."""

    operator_name = cast(str | None, metadata.get("operator_name"))
    operator_email = cast(str | None, metadata.get("operator_email"))
    if not operator_name or not operator_email:
        logger.info(
            "Missing operator identity metadata; skipping rewrite/local publication."
        )
        return None
    operator = OperatorIdentity(name=operator_name, email=operator_email)

    workspace = run_paths.workspace
    stash_created = False
    stash_conflicts = False
    try:
        if workspace_has_changes_fn(workspace):
            logger.info("Stashing workspace state before rewrite (%s)", STASH_MESSAGE)
            _ = run_git_cmd(workspace, ["stash", "push", "-u", "-m", STASH_MESSAGE])
            stash_created = True

        current = current_branch_fn(workspace)
        if current != target_branch:
            logger.info(
                "Checking out %s before rewrite (current branch %s)",
                target_branch,
                current,
            )
            _ = run_git_cmd(workspace, ["checkout", target_branch])

        upstream_anchor = run_git_cmd(workspace, ["rev-parse", upstream_ref]).strip()
        current_head = run_git_cmd(workspace, ["rev-parse", "HEAD"]).strip()
        rewrite_anchor = ensure_rewrite_anchor_branch(
            workspace,
            target_branch,
            upstream_anchor,
            run_git_cmd=run_git_cmd,
        )
        rewrite_range = f"{rewrite_anchor}..{target_branch}"

        if current_head == upstream_anchor:
            logger.info(
                "Branch %s head %s matches %s; skipping rewrite/local publication.",
                target_branch,
                current_head[:12],
                upstream_ref,
            )
            if stash_created:
                stash_conflicts = not pop_stash(workspace, run_git_cmd=run_git_cmd)
            return RewriteResult(
                branch=target_branch,
                operator=operator,
                rewrite_range=rewrite_range,
                publication_branch=None,
                stash_created=stash_created,
                stash_conflicts=stash_conflicts,
                rewritten=False,
                published=False,
            )

        validate_filter_repo(workspace, run_git_cmd=run_git_cmd)
        mailmap_path = write_mailmap(run_paths.run_dir, operator)
        try:
            _ = run_git_cmd(
                workspace,
                [
                    "filter-repo",
                    "--force",
                    f"--mailmap={mailmap_path}",
                    f"--refs={rewrite_range}",
                ],
            )
        finally:
            try:
                mailmap_path.unlink()
            except FileNotFoundError:
                pass

        assert_no_agent_commits(workspace, rewrite_range, run_git_cmd=run_git_cmd)

        ensure_upstream_merged_fn(workspace, upstream_ref, target_branch)
        post_rewrite_upstream = run_git_cmd(workspace, ["rev-parse", upstream_ref]).strip()
        if post_rewrite_upstream != upstream_anchor:
            logger.error(
                "Rewrite boundary violation: %s moved from %s to %s.",
                upstream_ref,
                upstream_anchor[:12],
                post_rewrite_upstream[:12],
            )
            raise SystemExit(1)

        publication_branch = build_publication_branch(metadata, target_branch)
        publish_to_local(
            workspace,
            repo_path,
            target_branch,
            publication_branch,
            run_git_cmd=run_git_cmd,
        )

        if stash_created:
            stash_conflicts = not pop_stash(workspace, run_git_cmd=run_git_cmd)

        return RewriteResult(
            branch=target_branch,
            operator=operator,
            rewrite_range=rewrite_range,
            publication_branch=publication_branch,
            stash_created=stash_created,
            stash_conflicts=stash_conflicts,
            rewritten=True,
            published=True,
        )
    except GitError as exc:
        logger.exception("Failed to rewrite/publish branch locally: %s", exc)
        if stash_created:
            logger.warning(
                "Stash '%s' remains on the stack; recover it later via `git stash list` inside %s.",
                STASH_MESSAGE,
                workspace,
            )
        raise SystemExit(1) from exc


def log_rewrite_summary(repo_path: Path, result: RewriteResult | None) -> None:
    """Log a concise summary of rewrite/local publication outcomes for operators."""

    if result is None:
        logger.info("Rewrite/local publication pipeline skipped (metadata incomplete).")
        return
    if not result.rewritten:
        logger.info(
            "Branch %s already matches rewrite anchor; no rewrite/local publication required.",
            result.branch,
        )
        if result.stash_created:
            if result.stash_conflicts:
                logger.warning(
                    "Stash '%s' reapplied with conflicts; inspect the workspace and use `git stash list` if needed.",
                    STASH_MESSAGE,
                )
            else:
                logger.info("Stash reapplied cleanly", stash=STASH_MESSAGE)
        return

    logger.info(
        "Authorship rewrite complete for %s with commits rewritten to %s <%s>.",
        result.rewrite_range,
        result.operator.name,
        result.operator.email,
    )
    if result.published and result.publication_branch:
        logger.info(
            "Published rewritten branch locally: %s -> %s",
            result.branch,
            result.publication_branch,
        )
        logger.info(
            "Local review handoff: inspect from %s using `git log --oneline %s..%s` and `git diff --stat %s...%s`.",
            repo_path,
            result.branch,
            result.publication_branch,
            result.branch,
            result.publication_branch,
        )
        logger.info("No GitHub push performed; publish manually after review if desired.")
    if result.stash_created:
        if result.stash_conflicts:
            logger.warning(
                "Stash '%s' reapplied with conflicts; recover manually via `git stash list` and resolve merges.",
                STASH_MESSAGE,
            )
        else:
            logger.info("Stash reapplied cleanly", stash=STASH_MESSAGE)
