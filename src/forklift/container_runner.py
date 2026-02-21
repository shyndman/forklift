from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping, Sequence
from uuid import uuid4

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = os.environ.get("FORKLIFT_DOCKER_IMAGE", "forklift/kitchen-sink:latest")
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("FORKLIFT_TIMEOUT_SECONDS", "210"))
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
DEFAULT_EXTRA_RUN_ARGS = shlex.split(os.environ.get("FORKLIFT_DOCKER_ARGS", ""))
SENSITIVE_ENV_KEYS = {"OPENCODE_API_KEY", "OPENCODE_SERVER_PASSWORD"}
HARNESS_ENTRYPOINT = "/opt/opencode/entrypoint.sh"
OPENCODE_LOG_DIR = "/home/forklift/.local/share/opencode/log"


@dataclass(frozen=True)
class ContainerRunResult:
    exit_code: int
    timed_out: bool
    stdout: str
    stderr: str
    container_name: str


class ContainerRunner:
    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        extra_run_args: Sequence[str] | None = None,
    ) -> None:
        self.image: str = image
        self.timeout_seconds: int = timeout_seconds
        self.extra_run_args: list[str] = list(extra_run_args or DEFAULT_EXTRA_RUN_ARGS)

    def run(
        self,
        workspace: Path,
        harness_state: Path,
        opencode_logs: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> ContainerRunResult:
        container_name = self._container_name(workspace)
        cmd = self._build_command(
            container_name,
            workspace,
            harness_state,
            opencode_logs,
            extra_env,
        )
        logger.info(
            "Launching container %s with timeout %s seconds (image=%s)",
            container_name,
            self.timeout_seconds,
            self.image,
        )
        logger.debug("Container command: %s", " ".join(self._mask_sensitive(cmd)))

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        timed_out = False
        stdout = ""
        stderr = ""

        try:
            stdout, stderr = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.warning(
                "Container %s exceeded %s seconds; forcing stop",
                container_name,
                self.timeout_seconds,
            )
            self._force_stop(container_name)
            process.kill()
            stdout_partial, stderr_partial = process.communicate()
            stdout = stdout_partial or ""
            stderr = stderr_partial or ""

        exit_code = process.returncode or 0
        return ContainerRunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout or "",
            stderr=stderr or "",
            container_name=container_name,
        )

    def _build_command(
        self,
        container_name: str,
        workspace: Path,
        harness_state: Path,
        opencode_logs: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> list[str]:
        env_flags: list[str] = []
        if extra_env:
            for key, value in sorted(extra_env.items()):
                env_flags.extend(["-e", f"{key}={value}"])
        cmd = [
            DOCKER_BIN,
            "run",
            "--rm",
            "--name",
            container_name,
            "-v",
            f"{workspace}:/workspace",
            "-v",
            f"{harness_state}:/harness-state",
            "-v",
            f"{opencode_logs}:{OPENCODE_LOG_DIR}",
            *self.extra_run_args,
            *env_flags,
            self.image,
            HARNESS_ENTRYPOINT,
        ]
        return cmd

    def _mask_sensitive(self, cmd: Sequence[str]) -> list[str]:
        masked = list(cmd)
        for idx in range(len(masked) - 1):
            if masked[idx] != "-e":
                continue
            assignment = masked[idx + 1]
            key, sep, _ = assignment.partition("=")
            if sep and key in SENSITIVE_ENV_KEYS:
                masked[idx + 1] = f"{key}=***"
        return masked

    def _force_stop(self, container_name: str) -> None:
        stop_cmd = [DOCKER_BIN, "kill", container_name]
        try:
            _ = subprocess.run(stop_cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.error("Failed to kill container %s: %s", container_name, exc)

    def _container_name(self, workspace: Path) -> str:
        ts = time.strftime("%Y%m%d%H%M%S")
        suffix = uuid4().hex[:6]
        project = workspace.parent.name.replace("_", "-")
        return f"forklift-{project}-{ts}-{suffix}".replace("--", "-")

