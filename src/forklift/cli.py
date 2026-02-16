from __future__ import annotations

import json

import logging
from pathlib import Path
from typing import cast, override

from clypi import Command

from .git import (
    GitError,
    GitFetchResult,
    GitRemote,
    current_branch,
    ensure_required_remotes,
    ensure_upstream_merged,
    fetch_remotes,
    run_git,
)

from .container_runner import ContainerRunner
from .run_manager import RunDirectoryManager, RunPaths


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

STUCK_EXIT_CODE = 4
STUCK_PREVIEW_LINES = 40


class Forklift(Command):
    """Primary entrypoint for the Forklift host orchestrator."""

    repo: Path | str | None = None
    verbose: bool = False

    @override
    async def run(self) -> None:
        repo_path = self._resolve_repo_path()
        self._configure_logging()
        logging.info("Starting Forklift orchestration in %s", repo_path)

        remotes = self._discover_required_remotes(repo_path)
        fetch_results = self._fetch_all(repo_path, remotes)

        for result in fetch_results:
            if result.output:
                logging.info("Fetch output for %s:\n%s", result.name, result.output)
            else:
                logging.info("Fetch output for %s: up to date", result.name)

        logging.info("Remote discovery and fetch complete.")

        run_manager = RunDirectoryManager()
        run_paths = run_manager.prepare(repo_path)
        logging.info(
            "Run directory ready at %s (workspace=%s, harness-state=%s)",
            run_paths.run_dir,
            run_paths.workspace,
            run_paths.harness_state,
        )

        container_runner = ContainerRunner()
        container_result = container_runner.run(run_paths.workspace, run_paths.harness_state)
        if container_result.stdout.strip():
            logging.info("Container stdout:\n%s", container_result.stdout.strip())
        if container_result.stderr.strip():
            logging.info("Container stderr:\n%s", container_result.stderr.strip())
        if container_result.timed_out:
            logging.error(
                "Container %s timed out after %s seconds",
                container_result.container_name,
                container_runner.timeout_seconds,
            )
            raise SystemExit(2)
        if container_result.exit_code != 0:
            logging.error(
                "Container %s exited with code %s",
                container_result.container_name,
                container_result.exit_code,
            )
            raise SystemExit(container_result.exit_code)
        logging.info("Container run completed successfully.")
        self._post_container_results(repo_path, run_paths)




    def _configure_logging(self) -> None:
        level = logging.DEBUG if self.verbose else logging.INFO
        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(level=level, format=LOG_FORMAT)
        else:
            root.setLevel(level)

    def _resolve_repo_path(self) -> Path:
        raw = self.repo
        if raw is None:
            base = Path.cwd()
        else:
            base = Path(raw)
        return base.expanduser().resolve()

    def _discover_required_remotes(self, repo_path: Path) -> dict[str, GitRemote]:
        try:
            remotes = ensure_required_remotes(repo_path)
        except GitError as exc:
            logging.error("%s", exc)
            raise SystemExit(1) from exc

        for remote in remotes.values():
            logging.info("Detected remote %s -> %s", remote.name, remote.fetch_url)
        return remotes

    def _fetch_all(
        self, repo_path: Path, remotes: dict[str, GitRemote]
    ) -> list[GitFetchResult]:
        try:
            return fetch_remotes(repo_path, remotes)
        except GitError as exc:
            logging.error("%s", exc)
            raise SystemExit(1) from exc

    def _post_container_results(self, repo_path: Path, run_paths: RunPaths) -> None:
        metadata = self._load_run_metadata(run_paths.run_dir)
        workspace = run_paths.workspace
        self._fail_if_stuck(workspace)

        target_branch = metadata.get("main_branch") or current_branch(workspace)
        upstream_sha = metadata.get("upstream_main_sha")

        if upstream_sha:
            try:
                ensure_upstream_merged(workspace, upstream_sha, target_branch)
                logging.info(
                    "Verified upstream commit %s is ancestor of %s",
                    upstream_sha[:12],
                    target_branch,
                )
            except GitError as exc:
                logging.error("Upstream verification failed: %s", exc)
                raise SystemExit(3) from exc
        else:
            logging.warning("Upstream commit info missing; skipping verification.")

        if self._workspace_has_changes(workspace):
            self._create_pr_stub(repo_path, target_branch)
        else:
            logging.info("No workspace changes detected; skipping PR creation.")

    def _workspace_has_changes(self, workspace: Path) -> bool:
        status = run_git(workspace, ["status", "--porcelain"])
        return bool(status.strip())

    def _fail_if_stuck(self, workspace: Path) -> None:
        stuck_file = workspace / "STUCK.md"
        if not stuck_file.exists():
            return
        logging.warning("STUCK.md detected at %s; skipping verification and PR.", stuck_file)
        try:
            contents = stuck_file.read_text().strip()
        except OSError as exc:
            logging.warning("Unable to read STUCK.md: %s", exc)
        else:
            if contents:
                preview_lines = contents.splitlines()[:STUCK_PREVIEW_LINES]
                preview_text = "\n".join(preview_lines)
                logging.warning(
                    "STUCK.md preview (first %s lines):\n%s",
                    STUCK_PREVIEW_LINES,
                    preview_text,
                )
            else:
                logging.warning("STUCK.md is empty.")
        raise SystemExit(STUCK_EXIT_CODE)


    def _load_run_metadata(self, run_dir: Path) -> dict[str, str | None]:
        metadata_path = run_dir / "metadata.json"
        try:
            raw = metadata_path.read_text()
        except FileNotFoundError:
            logging.warning("Metadata file missing at %s", metadata_path)
            return {}
        data = cast(dict[str, str | None], json.loads(raw))
        return data

    def _create_pr_stub(self, repo_path: Path, branch: str) -> None:
        logging.info(
            "PR stub: use host repo at %s to push branch %s and run `gh pr create --head %s --base main`.",
            repo_path,
            branch,
            branch,
        )

