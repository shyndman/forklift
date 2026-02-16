from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import os

import json
import shutil

import logging
from pathlib import Path
import subprocess
from typing import cast

CONTAINER_UID = 1000
CONTAINER_GID = 1000



logger = logging.getLogger(__name__)

DEFAULT_RUNS_ROOT = Path.home() / "forklift" / "runs"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


class RunDirectoryError(RuntimeError):
    """Raised when workspace preparation fails."""


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    workspace: Path
    harness_state: Path


class RunDirectoryManager:
    def __init__(self, runs_root: Path | None = None) -> None:
        self._runs_root: Path = (runs_root or DEFAULT_RUNS_ROOT).expanduser().resolve()

    def prepare(self, source_repo: Path) -> RunPaths:
        source_repo = source_repo.resolve()
        timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
        project = source_repo.name
        run_dir = self._runs_root / f"{project}_{timestamp}"
        workspace = run_dir / "workspace"
        harness_state = run_dir / "harness-state"

        logger.info("Creating run directory at %s", run_dir)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        harness_state.mkdir(parents=True, exist_ok=True)

        self._clone_repo(source_repo, workspace)
        self._overlay_fork_context(source_repo, workspace)

        branch_info = self._capture_branch_info(workspace, source_repo)
        self._remove_remotes(workspace)
        self._ensure_permissions(workspace, harness_state)
        self._write_metadata(run_dir, source_repo, timestamp, branch_info)

        return RunPaths(run_dir=run_dir, workspace=workspace, harness_state=harness_state)

    def _clone_repo(self, source: Path, destination: Path) -> None:
        if destination.exists():
            raise RunDirectoryError(f"Workspace already exists at {destination}")
        logger.info("Cloning %s -> %s", source, destination)
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                ["git", "clone", "--shared", str(source), str(destination)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            output = cast(str | None, exc.stdout) or ""
            raise RunDirectoryError(
                f"Failed to clone {source} -> {destination}: {output.strip()}"
            ) from exc
        stdout_text = cast(str | None, result.stdout) or ""
        logger.debug("git clone output:\n%s", stdout_text.strip())

    def _remove_remotes(self, workspace: Path) -> None:
        logger.info("Removing remotes inside %s", workspace)
        remotes_output = self._run_git(workspace, ["remote"]).strip()
        remote_names: list[str] = [
            line.strip() for line in remotes_output.splitlines() if line.strip()
        ]
        for remote in remote_names:
            _ = self._run_git(workspace, ["remote", "remove", remote])
        logger.debug("All remotes removed from %s", workspace)

    def _ensure_permissions(self, workspace: Path, harness_state: Path) -> None:
        logger.info(
            "Aligning workspace and harness-state ownership to %s:%s",
            CONTAINER_UID,
            CONTAINER_GID,
        )
        for path in (workspace, harness_state):
            self._chown_recursive(path, CONTAINER_UID, CONTAINER_GID)

    def _chown_recursive(self, path: Path, uid: int, gid: int) -> None:
        try:
            os.chown(path, uid, gid)
        except PermissionError as exc:
            raise RunDirectoryError(
                f"Failed to set ownership on {path}: {exc}"  # noqa: TRY003
            ) from exc
        if path.is_dir():
            for child in path.iterdir():
                self._chown_recursive(child, uid, gid)


    def _write_metadata(
        self,
        run_dir: Path,
        source_repo: Path,
        timestamp: str,
        extra: dict[str, str | None],
    ) -> None:
        metadata = {
            "source_repo": str(source_repo),
            "created_at": timestamp,
            **extra,
        }
        _ = (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    def _overlay_fork_context(self, source_repo: Path, workspace: Path) -> None:
        fork_file = source_repo / "FORK.md"
        if not fork_file.exists():
            logger.debug("No FORK.md found at %s", fork_file)
            return
        destination = workspace / "FORK.md"
        _ = shutil.copy2(fork_file, destination)
        logger.debug("Copied FORK.md into %s", destination)



    def _capture_branch_info(
        self, workspace: Path, source_repo: Path
    ) -> dict[str, str | None]:
        info: dict[str, str | None] = {}
        try:
            info["main_branch"] = self._run_git(
                workspace, ["rev-parse", "--abbrev-ref", "HEAD"]
            )
        except RunDirectoryError as exc:
            logger.warning("Failed to capture branch name: %s", exc)
            info["main_branch"] = None
        try:
            info["upstream_main_sha"] = self._run_git(
                source_repo, ["rev-parse", "upstream/main"]
            )
        except RunDirectoryError:
            info["upstream_main_sha"] = None
        return info


    def _run_git(self, repo_path: Path, args: Sequence[str]) -> str:
        try:
            completed: subprocess.CompletedProcess[str] = subprocess.run(
                ["git", *args],
                cwd=repo_path,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            output = cast(str | None, exc.stdout) or ""
            raise RunDirectoryError(
                f"git {' '.join(args)} failed in {repo_path}: {output.strip()}"
            ) from exc
        return (completed.stdout or "").strip()

