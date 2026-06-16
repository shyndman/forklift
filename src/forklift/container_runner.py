from __future__ import annotations

import contextvars
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
from typing import IO, cast
from uuid import uuid4

import structlog
from structlog.stdlib import BoundLogger

from .cli_runtime import DEFAULT_RUN_TIMEOUT_SECONDS
from .run_state import RunStateError, update_run_state, utc_now_iso8601

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))
harness_logger: BoundLogger = cast(
    BoundLogger, structlog.get_logger("forklift.harness")
)

DEFAULT_IMAGE = os.environ.get("FORKLIFT_DOCKER_IMAGE", "forklift/kitchen-sink:latest")
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
DEFAULT_EXTRA_RUN_ARGS = shlex.split(os.environ.get("FORKLIFT_DOCKER_ARGS", ""))
SENSITIVE_ENV_KEYS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
}
HARNESS_ENTRYPOINT = "/opt/forklift/harness/entrypoint.sh"
CONTROL_MOUNT_DIR = "/forklift-control"
LOG_SOCKET_NAME = "log.sock"
LOG_SOCKET_ENV = "FORKLIFT_LOG_SOCK"
MAX_UNIX_SOCKET_PATH_BYTES = 107
# Safety bound on joining the pipe-drain workers after the container exits; the
# pipes close on exit so the threads normally finish immediately.
STREAM_JOIN_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class ContainerRunResult:
    exit_code: int
    timed_out: bool
    stdout: str
    stderr: str
    container_name: str


LogRecordCallback = Callable[[dict[str, object]], None]

# Maps harness-authored structlog level names to the host logger method that
# re-emits the record. Unmapped levels fall back to ``info``.
_HARNESS_LEVEL_METHOD_NAMES: dict[str, str] = {
    "warning": "warning",
    "warn": "warning",
    "error": "error",
    "err": "error",
    "critical": "critical",
    "fatal": "critical",
    "debug": "debug",
}


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
        control_dir: Path,
        run_state_file: Path,
        extra_env: Mapping[str, str] | None = None,
        record_callback: LogRecordCallback | None = None,
    ) -> ContainerRunResult:
        """Run the sandbox container and record lifecycle transitions in run-state metadata."""

        socket_path = control_dir / LOG_SOCKET_NAME
        listener: socket.socket | None = None
        listener_thread: threading.Thread | None = None
        stop_event = threading.Event()
        callback = record_callback or self._render_log_record

        try:
            listener, listener_thread = self._start_log_record_listener(
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
            self._stop_log_record_listener(
                listener, listener_thread, stop_event, socket_path
            )
            raise

        self._safe_update_run_state(
            run_state_file,
            status="running",
            container_started_at=utc_now_iso8601(),
        )
        timed_out = False
        # Drain the container's pipes in worker threads: both stdout and stderr are
        # surfaced live on the host logger so the operator sees the in-container
        # harness telemetry in the original CLI stream as it happens. stderr is also
        # buffered and returned in ContainerRunResult.stderr for diagnostics. Each
        # worker gets its own copy_context() — a single contextvars.Context cannot be
        # entered concurrently — both carrying the run=<id> correlator.
        stdout_ctx = contextvars.copy_context()
        stderr_ctx = contextvars.copy_context()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        stdout_thread = threading.Thread(
            target=stdout_ctx.run,
            args=(self._drain_stream, process.stdout, stdout_lines, True),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_ctx.run,
            args=(self._drain_stream, process.stderr, stderr_lines, True),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        try:
            _ = process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.warning(
                "Container exceeded timeout",
                container=container_name,
                timeout_seconds=self.timeout_seconds,
            )
            self._force_stop(container_name)
            process.kill()
            _ = process.wait()
        finally:
            stdout_thread.join(timeout=STREAM_JOIN_TIMEOUT_SECONDS)
            stderr_thread.join(timeout=STREAM_JOIN_TIMEOUT_SECONDS)
            self._stop_log_record_listener(
                listener, listener_thread, stop_event, socket_path
            )

        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)

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

    def _drain_stream(
        self, pipe: IO[str] | None, sink: list[str], surface: bool
    ) -> None:
        """Drain a process pipe line-by-line, buffering and optionally surfacing it.

        Surfaced lines (both stdout and stderr) are re-emitted on the host logger
        so the operator sees the in-container harness telemetry live in the
        original CLI stream under the run=<id> correlator (this worker runs inside
        a copied context so the binding propagates here).
        """

        if pipe is None:
            return
        with pipe:
            for raw_line in pipe:
                sink.append(raw_line)
                if surface:
                    line = raw_line.rstrip("\n")
                    if line:
                        logger.info(line)

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
        env[LOG_SOCKET_ENV] = f"{CONTROL_MOUNT_DIR}/{LOG_SOCKET_NAME}"
        return env

    def _start_log_record_listener(
        self,
        socket_path: Path,
        stop_event: threading.Event,
        record_callback: LogRecordCallback,
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
        # Copy the current context so the listener thread's re-emitted records
        # inherit the run=<id> correlator bound on the host before the run.
        ctx = contextvars.copy_context()
        listener_thread = threading.Thread(
            target=ctx.run,
            args=(self._listen_for_log_records, listener, stop_event, record_callback),
            daemon=True,
            name="forklift-log-records",
        )
        listener_thread.start()
        return listener, listener_thread

    def _listen_for_log_records(
        self,
        listener: socket.socket,
        stop_event: threading.Event,
        record_callback: LogRecordCallback,
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
                    "Log record listener accept failed",
                    error=str(exc),
                )
                break

            with connection:
                self._read_log_connection(connection, stop_event, record_callback)

    def _read_log_connection(
        self,
        connection: socket.socket,
        stop_event: threading.Event,
        record_callback: LogRecordCallback,
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
                    "Log record connection read failed",
                    error=str(exc),
                )
                return
            if not chunk:
                break
            chunks.append(chunk)

        if not chunks:
            return
        self._dispatch_log_payload(b"".join(chunks), record_callback)

    def _dispatch_log_payload(
        self,
        payload: bytes,
        record_callback: LogRecordCallback,
    ) -> None:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.warning(
                "Ignoring non-UTF-8 harness log payload",
                error=str(exc),
            )
            return

        for line in text.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            record = self._parse_log_record(raw_line)
            if record is None:
                continue
            try:
                record_callback(record)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception(
                    "Harness log record callback failed",
                    error=exc,
                )

    def _parse_log_record(self, raw_line: str) -> dict[str, object] | None:
        try:
            payload_obj = cast(object, json.loads(raw_line))
        except json.JSONDecodeError:
            logger.warning("Unparseable harness log", payload=raw_line)
            return None
        if not isinstance(payload_obj, dict):
            logger.warning("Unparseable harness log", payload=raw_line)
            return None
        return cast(dict[str, object], payload_obj)

    def _render_log_record(self, record: dict[str, object]) -> None:
        """Re-emit one harness-authored record on the host ``forklift.harness`` logger."""

        event = record.pop("event", None)
        if isinstance(event, str):
            message = event
        else:
            message = "harness log"
            if event is not None:
                record["raw_event"] = event
        level = record.pop("level", None)
        _ = record.pop("timestamp", None)
        self._harness_log_method(level)(message, **record)

    def _harness_log_method(self, level: object) -> Callable[..., None]:
        name = (
            _HARNESS_LEVEL_METHOD_NAMES.get(level, "info")
            if isinstance(level, str)
            else "info"
        )
        return cast("Callable[..., None]", getattr(harness_logger, name))

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

    def _stop_log_record_listener(
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
