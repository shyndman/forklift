from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

import structlog
from structlog.stdlib import BoundLogger

from .cli_runtime import DEFAULT_RUN_TIMEOUT_SECONDS
from .run_state import RunStateError, update_run_state, utc_now_iso8601

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))

DEFAULT_IMAGE = os.environ.get("FORKLIFT_DOCKER_IMAGE", "forklift/kitchen-sink:latest")
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
DEFAULT_EXTRA_RUN_ARGS = shlex.split(os.environ.get("FORKLIFT_DOCKER_ARGS", ""))
SENSITIVE_ENV_KEYS = {"OPENCODE_API_KEY", "OPENCODE_SERVER_PASSWORD"}
HARNESS_ENTRYPOINT = "/opt/opencode/entrypoint.sh"
OPENCODE_LOG_DIR = "/home/forklift/.local/share/opencode/log"
CONTROL_MOUNT_DIR = "/forklift-control"
REBASE_EVENTS_SOCKET_NAME = "rebase-events.sock"
REBASE_EVENTS_SOCKET_ENV = "FORKLIFT_REBASE_EVENTS_SOCK"
REBASE_EVENT_VERSION = 1
MAX_UNIX_SOCKET_PATH_BYTES = 107
KNOWN_REBASE_EVENTS = frozenset(
    {"progress", "conflict", "continue", "skip", "auto_skip", "complete", "abort"}
)


@dataclass(frozen=True)
class ContainerRunResult:
    exit_code: int
    timed_out: bool
    stdout: str
    stderr: str
    container_name: str


@dataclass(frozen=True)
class RebaseEvent:
    event: str
    step: int
    total: int
    sha: str | None = None
    subject: str | None = None
    files: tuple[str, ...] = ()


RebaseEventCallback = Callable[[RebaseEvent], None]


class ContainerRunner:
    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
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
        control_dir: Path,
        run_state_file: Path,
        extra_env: Mapping[str, str] | None = None,
        event_callback: RebaseEventCallback | None = None,
    ) -> ContainerRunResult:
        """Run the sandbox container and record lifecycle transitions in run-state metadata."""

        socket_path = control_dir / REBASE_EVENTS_SOCKET_NAME
        listener: socket.socket | None = None
        listener_thread: threading.Thread | None = None
        stop_event = threading.Event()
        callback = event_callback or self._log_rebase_event

        try:
            listener, listener_thread = self._start_rebase_event_listener(
                socket_path,
                stop_event,
                callback,
            )
        except (OSError, ValueError):
            self._safe_update_run_state(
                run_state_file,
                status="failed",
                finished_at=utc_now_iso8601(),
                exit_code=-1,
            )
            raise

        container_name = self._container_name(workspace)
        cmd = self._build_command(
            container_name,
            workspace,
            harness_state,
            opencode_logs,
            control_dir,
            self._build_container_env(extra_env),
        )
        logger.info(
            "Launching container",
            container=container_name,
            timeout_seconds=self.timeout_seconds,
            image=self.image,
        )
        logger.debug(
            "Container command",
            command=" ".join(self._mask_sensitive(cmd)),
        )

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            self._safe_update_run_state(
                run_state_file,
                status="failed",
                finished_at=utc_now_iso8601(),
                exit_code=-1,
            )
            self._stop_rebase_event_listener(listener, listener_thread, stop_event, socket_path)
            raise

        self._safe_update_run_state(
            run_state_file,
            status="running",
            container_started_at=utc_now_iso8601(),
        )
        client_log_path = harness_state / "opencode-client.log"
        logger.info("Agent log available", path=client_log_path)
        timed_out = False
        stdout = ""
        stderr = ""

        try:
            stdout, stderr = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.warning(
                "Container exceeded timeout",
                container=container_name,
                timeout_seconds=self.timeout_seconds,
            )
            self._force_stop(container_name)
            process.kill()
            stdout_partial, stderr_partial = process.communicate()
            stdout = stdout_partial or ""
            stderr = stderr_partial or ""
        finally:
            self._stop_rebase_event_listener(listener, listener_thread, stop_event, socket_path)

        exit_code = process.returncode or 0
        final_status = (
            "timed_out" if timed_out else "completed" if exit_code == 0 else "failed"
        )
        self._safe_update_run_state(
            run_state_file,
            status=final_status,
            finished_at=utc_now_iso8601(),
            exit_code=exit_code,
        )
        return ContainerRunResult(
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout or "",
            stderr=stderr or "",
            container_name=container_name,
        )

    def _safe_update_run_state(self, run_state_file: Path, **updates: object) -> None:
        """Best-effort helper that keeps run-state transitions visible without crashing runs."""

        try:
            _ = update_run_state(run_state_file, **updates)
        except RunStateError as exc:
            logger.warning(
                "Unable to persist run-state transition",
                path=run_state_file,
                error=str(exc),
                updates=updates,
            )

    def _build_command(
        self,
        container_name: str,
        workspace: Path,
        harness_state: Path,
        opencode_logs: Path,
        control_dir: Path,
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
            "-v",
            f"{control_dir}:{CONTROL_MOUNT_DIR}",
            *self.extra_run_args,
            *env_flags,
            self.image,
            HARNESS_ENTRYPOINT,
        ]
        return cmd

    def _build_container_env(
        self, extra_env: Mapping[str, str] | None
    ) -> dict[str, str]:
        env = dict(extra_env or {})
        env[REBASE_EVENTS_SOCKET_ENV] = (
            f"{CONTROL_MOUNT_DIR}/{REBASE_EVENTS_SOCKET_NAME}"
        )
        return env

    def _start_rebase_event_listener(
        self,
        socket_path: Path,
        stop_event: threading.Event,
        event_callback: RebaseEventCallback,
    ) -> tuple[socket.socket, threading.Thread]:
        self._validate_unix_socket_path(socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket_path(socket_path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.settimeout(0.2)
            listener.bind(str(socket_path))
            os.chmod(socket_path, 0o666)
            listener.listen()
        except Exception:
            listener.close()
            self._remove_stale_socket_path(socket_path)
            raise
        listener_thread = threading.Thread(
            target=self._listen_for_rebase_events,
            args=(listener, stop_event, event_callback),
            daemon=True,
            name="forklift-rebase-events",
        )
        listener_thread.start()
        return listener, listener_thread

    def _listen_for_rebase_events(
        self,
        listener: socket.socket,
        stop_event: threading.Event,
        event_callback: RebaseEventCallback,
    ) -> None:
        while not stop_event.is_set():
            try:
                accepted = cast(tuple[socket.socket, object], listener.accept())
                connection = accepted[0]
            except socket.timeout:
                continue
            except OSError as exc:
                if stop_event.is_set():
                    break
                logger.warning(
                    "Rebase event listener accept failed",
                    error=str(exc),
                )
                break

            with connection:
                self._read_rebase_event_connection(connection, stop_event, event_callback)

    def _read_rebase_event_connection(
        self,
        connection: socket.socket,
        stop_event: threading.Event,
        event_callback: RebaseEventCallback,
    ) -> None:
        connection.settimeout(0.2)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                if stop_event.is_set():
                    return
                continue
            except OSError as exc:
                logger.warning(
                    "Rebase event connection read failed",
                    error=str(exc),
                )
                return
            if not chunk:
                break
            chunks.append(chunk)

        if not chunks:
            return
        self._dispatch_rebase_event_payload(b"".join(chunks), event_callback)

    def _dispatch_rebase_event_payload(
        self,
        payload: bytes,
        event_callback: RebaseEventCallback,
    ) -> None:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.warning(
                "Ignoring non-UTF-8 rebase event payload",
                error=str(exc),
            )
            return

        for line in text.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            event = self._parse_rebase_event(raw_line)
            if event is None:
                continue
            try:
                event_callback(event)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception(
                    "Rebase event callback failed",
                    rebase_event=event.event,
                    error=exc,
                )

    def _parse_rebase_event(self, raw_line: str) -> RebaseEvent | None:
        try:
            payload_obj = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Ignoring malformed rebase event payload",
                error=str(exc),
                payload=raw_line,
            )
            return None
        if not isinstance(payload_obj, dict):
            logger.warning(
                "Ignoring non-object rebase event payload",
                payload=payload_obj,
            )
            return None

        payload = cast(dict[str, object], payload_obj)

        version = payload.get("v")
        event_name = payload.get("event")
        step = payload.get("step")
        total = payload.get("total")
        files = payload.get("files")

        if version != REBASE_EVENT_VERSION:
            logger.warning(
                "Ignoring unknown rebase event version",
                version=version,
                payload=payload,
            )
            return None
        if not isinstance(event_name, str) or event_name not in KNOWN_REBASE_EVENTS:
            logger.warning(
                "Ignoring unknown rebase event type",
                rebase_event=event_name,
                payload=payload,
            )
            return None
        if not self._is_valid_ordinal(step) or not self._is_valid_ordinal(total):
            logger.warning(
                "Ignoring rebase event with invalid ordinals",
                step=step,
                total=total,
                payload=payload,
            )
            return None
        if files is not None and not isinstance(files, list):
            logger.warning(
                "Ignoring rebase event with invalid files payload",
                files=files,
                payload=payload,
            )
            return None
        normalized_files: tuple[str, ...] = ()
        if isinstance(files, list):
            if any(not isinstance(item, str) for item in files):
                logger.warning(
                    "Ignoring rebase event with invalid files payload",
                    files=files,
                    payload=payload,
                )
                return None
            normalized_files = tuple(cast(list[str], files))

        if files is not None and not normalized_files and files != []:
            logger.warning(
                "Ignoring rebase event with invalid files payload",
                files=files,
                payload=payload,
            )
            return None

        sha = payload.get("sha")
        subject = payload.get("subject")
        return RebaseEvent(
            event=event_name,
            step=cast(int, step),
            total=cast(int, total),
            sha=sha if isinstance(sha, str) and sha else None,
            subject=subject if isinstance(subject, str) and subject else None,
            files=normalized_files,
        )

    def _is_valid_ordinal(self, value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _validate_unix_socket_path(self, socket_path: Path) -> None:
        encoded = os.fsencode(str(socket_path))
        if len(encoded) <= MAX_UNIX_SOCKET_PATH_BYTES:
            return
        raise ValueError(
            "Unix socket path exceeds Linux 107-byte pathname limit; shorten XDG_STATE_HOME or the repository/run path before retrying."
        )

    def _remove_stale_socket_path(self, socket_path: Path) -> None:
        try:
            socket_path.unlink()
        except FileNotFoundError:
            return

    def _stop_rebase_event_listener(
        self,
        listener: socket.socket | None,
        listener_thread: threading.Thread | None,
        stop_event: threading.Event,
        socket_path: Path,
    ) -> None:
        stop_event.set()
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if listener_thread is not None:
            listener_thread.join(timeout=1)
        self._remove_stale_socket_path(socket_path)

    def _log_rebase_event(self, event: RebaseEvent) -> None:
        fields: dict[str, object] = {
            "step": event.step,
            "total": event.total,
        }
        if event.sha is not None:
            fields["sha"] = event.sha
        if event.subject is not None:
            fields["subject"] = event.subject
        if event.files:
            fields["files"] = ", ".join(event.files)

        if event.event == "conflict":
            logger.warning(
                f"Conflict {event.step}/{event.total}",
                conflict_files=len(event.files),
                **fields,
            )
            return
        if event.event == "progress":
            logger.info(f"Rebase {event.step}/{event.total}", **fields)
            return
        if event.event == "complete":
            logger.info("Rebase complete", **fields)
            return

        title = event.event.replace("_", "-").title()
        logger.info(f"{title} {event.step}/{event.total}", **fields)

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
        stop_cmd = [DOCKER_BIN, "stop", "--time", "10", container_name]
        try:
            _ = subprocess.run(
                stop_cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.exception(
                "Failed to kill container",
                container=container_name,
                error=exc,
            )

    def _container_name(self, workspace: Path) -> str:
        ts = time.strftime("%Y%m%d%H%M%S")
        suffix = uuid4().hex[:6]
        project = workspace.parent.name.replace("_", "-")
        return f"forklift-{project}-{ts}-{suffix}".replace("--", "-")
