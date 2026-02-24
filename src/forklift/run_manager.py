from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import os
import secrets
import shutil
from pathlib import Path
import subprocess
from typing import cast

import structlog
from structlog.stdlib import BoundLogger

CONTAINER_UID = 1000
CONTAINER_GID = 1000


def _default_runs_root() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = (
        Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
    )
    return (base / "forklift" / "runs").resolve()


logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))

DEFAULT_RUNS_ROOT = _default_runs_root()
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


class RunDirectoryError(RuntimeError):
    """Raised when workspace preparation fails."""


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    workspace: Path
    harness_state: Path
    opencode_logs: Path
    run_id: str


class RunDirectoryManager:
    def __init__(self, runs_root: Path | None = None) -> None:
        self._runs_root: Path = (runs_root or DEFAULT_RUNS_ROOT).expanduser().resolve()

    def prepare(
        self,
        source_repo: Path,
        main_branch: str = "main",
        extra_metadata: dict[str, object] | None = None,
    ) -> RunPaths:
        source_repo = source_repo.resolve()
        timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
        project = source_repo.name
        run_dir = self._runs_root / f"{project}_{timestamp}"
        workspace = run_dir / "workspace"
        harness_state = run_dir / "harness-state"
        opencode_logs = run_dir / "opencode-logs"
        run_id = self._generate_run_id()

        logger.info("Creating run directory", run_dir=run_dir)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        harness_state.mkdir(parents=True, exist_ok=True)
        opencode_logs.mkdir(parents=True, exist_ok=True)

        self._clone_repo(source_repo, workspace)
        self._overlay_fork_context(source_repo, workspace)

        branch_info = self._capture_branch_info(source_repo, main_branch)
        metadata_payload: dict[str, object] = {**branch_info}
        if extra_metadata:
            metadata_payload.update(extra_metadata)
        metadata_payload["run_id"] = run_id
        upstream_main_sha = branch_info.get("upstream_main_sha")
        self._write_metadata(run_dir, source_repo, timestamp, metadata_payload)
        self._remove_remotes(workspace)
        self._seed_upstream_ref(workspace, upstream_main_sha, main_branch)
        self._ensure_permissions(workspace, harness_state, opencode_logs)

        return RunPaths(
            run_dir=run_dir,
            workspace=workspace,
            harness_state=harness_state,
            opencode_logs=opencode_logs,
            run_id=run_id,
        )

    def _clone_repo(self, source: Path, destination: Path) -> None:
        if destination.exists():
            raise RunDirectoryError(f"Workspace already exists at {destination}")
        logger.info("Cloning source repo", source=source, destination=destination)
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                ["git", "clone", str(source), str(destination)],
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
        logger.debug("git clone output", output=stdout_text.strip())

    def _remove_remotes(self, workspace: Path) -> None:
        logger.info("Removing remotes", workspace=workspace)
        remotes_output = self._run_git(workspace, ["remote"]).strip()
        remote_names: list[str] = [
            line.strip() for line in remotes_output.splitlines() if line.strip()
        ]
        for remote in remote_names:
            _ = self._run_git(workspace, ["remote", "remove", remote])
        logger.debug("All remotes removed", workspace=workspace)

    def _ensure_permissions(self, *paths: Path) -> None:
        logger.info(
            "Aligning run artifact ownership",
            uid=CONTAINER_UID,
            gid=CONTAINER_GID,
        )
        for path in paths:
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
        extra: dict[str, object],
    ) -> None:
        metadata = {
            "source_repo": str(source_repo),
            "created_at": timestamp,
            **extra,
        }
        _ = (run_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )

    def _overlay_fork_context(self, source_repo: Path, workspace: Path) -> None:
        fork_file = source_repo / "FORK.md"
        if not fork_file.exists():
            logger.debug("No FORK.md found", path=fork_file)
            return
        destination = workspace / "FORK.md"
        _ = shutil.copy2(fork_file, destination)
        logger.debug("Copied FORK.md", destination=destination)

    def _capture_branch_info(
        self, source_repo: Path, main_branch: str
    ) -> dict[str, str | None]:
        info: dict[str, str | None] = {"main_branch": main_branch}
        try:
            info["upstream_main_sha"] = self._run_git(
                source_repo, ["rev-parse", f"upstream/{main_branch}"]
            )
        except RunDirectoryError:
            info["upstream_main_sha"] = None
        try:
            info["origin_main_sha"] = self._run_git(
                source_repo, ["rev-parse", f"origin/{main_branch}"]
            )
        except RunDirectoryError:
            info["origin_main_sha"] = None
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

    def _seed_upstream_ref(
        self, workspace: Path, upstream_sha: str | None, main_branch: str
    ) -> None:
        if not upstream_sha:
            raise RunDirectoryError(
                f"Unable to seed upstream ref in the workspace; upstream_main_sha is missing. Ensure the source repo has an upstream remote with the '{main_branch}' branch."
            )
        remote_ref = f"refs/remotes/upstream/{main_branch}"
        helper_branch = f"upstream-{main_branch.replace('/', '-')}"
        logger.info(
            "Seeding synthetic upstream ref",
            remote_ref=remote_ref,
            upstream_sha=upstream_sha,
            helper_branch=helper_branch,
        )
        _ = self._run_git(workspace, ["update-ref", remote_ref, upstream_sha])
        _ = self._run_git(workspace, ["branch", "-f", helper_branch, upstream_sha])

    def _generate_run_id(self) -> str:
        """Return the four-character correlator shared across host and harness logs."""

        token = secrets.token_bytes(3)
        encoded = base64.urlsafe_b64encode(token).decode("ascii")
        return encoded[:4]
