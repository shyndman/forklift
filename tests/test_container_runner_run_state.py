from __future__ import annotations

import socket
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast
from unittest.mock import patch

from forklift.container_runner import (
    CONTROL_MOUNT_DIR,
    REBASE_EVENTS_SOCKET_ENV,
    REBASE_EVENTS_SOCKET_NAME,
    ContainerRunner,
    RebaseEvent,
)


class SuccessfulProcess:
    returncode: int

    def __init__(self) -> None:
        self.returncode = 0

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        _ = timeout
        return ("stdout", "stderr")


class EventEmittingProcess:
    returncode: int
    _socket_path: Path
    _payloads: list[bytes]

    def __init__(self, socket_path: Path, payloads: list[bytes]) -> None:
        self.returncode = 0
        self._socket_path = socket_path
        self._payloads = payloads

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        _ = timeout
        deadline = time.time() + 1
        while not self._socket_path.exists() and time.time() < deadline:
            time.sleep(0.01)
        for payload in self._payloads:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self._socket_path))
                client.sendall(payload)
            time.sleep(0.01)
        time.sleep(0.05)
        return ("stdout", "stderr")


class TimeoutProcess:
    _timed_out_once: bool
    returncode: int

    def __init__(self) -> None:
        self._timed_out_once = False
        self.returncode = -9

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        if not self._timed_out_once:
            self._timed_out_once = True
            raise subprocess.TimeoutExpired(cmd="docker", timeout=timeout or 1)
        return ("stdout-partial", "stderr-partial")

    def kill(self) -> None:
        return None


class ContainerRunnerRunStateTests(unittest.TestCase):
    def _make_paths(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        workspace = root / "workspace"
        harness_state = root / "harness-state"
        opencode_logs = root / "opencode-logs"
        control_dir = root / "control"
        run_state_file = root / "run-state.json"
        workspace.mkdir(parents=True)
        harness_state.mkdir(parents=True)
        opencode_logs.mkdir(parents=True)
        control_dir.mkdir(parents=True)
        return workspace, harness_state, opencode_logs, control_dir, run_state_file

    def test_updates_run_state_and_wires_control_mount_for_successful_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace, harness_state, opencode_logs, control_dir, run_state_file = (
                self._make_paths(root)
            )
            socket_path = control_dir / REBASE_EVENTS_SOCKET_NAME
            _ = socket_path.write_text("stale", encoding="utf-8")
            process = SuccessfulProcess()
            runner = ContainerRunner(timeout_seconds=5)

            with (
                patch(
                    "forklift.container_runner.subprocess.Popen", return_value=process
                ) as popen_mock,
                patch("forklift.container_runner.update_run_state") as update_state,
            ):
                result = runner.run(
                    workspace,
                    harness_state,
                    opencode_logs,
                    control_dir,
                    run_state_file,
                    extra_env={"FORKLIFT_MAIN_BRANCH": "main"},
                )

            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)
            statuses = [
                call.kwargs.get("status") for call in update_state.call_args_list
            ]
            self.assertEqual(statuses, ["running", "completed"])
            self.assertEqual(update_state.call_args_list[-1].kwargs.get("exit_code"), 0)

            command = cast(Sequence[str], popen_mock.call_args.args[0])
            self.assertIn("-v", command)
            self.assertIn(f"{control_dir}:{CONTROL_MOUNT_DIR}", command)
            self.assertIn("-e", command)
            self.assertIn(
                f"{REBASE_EVENTS_SOCKET_ENV}={CONTROL_MOUNT_DIR}/{REBASE_EVENTS_SOCKET_NAME}",
                command,
            )
            self.assertFalse(socket_path.exists())

    def test_rejects_overlength_unix_socket_path_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / ("segment-" * 8) / ("child-" * 8)
            workspace, harness_state, opencode_logs, control_dir, run_state_file = (
                self._make_paths(nested)
            )
            runner = ContainerRunner(timeout_seconds=5)

            with (
                patch("forklift.container_runner.subprocess.Popen") as popen_mock,
                patch("forklift.container_runner.update_run_state") as update_state,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "shorten XDG_STATE_HOME or the repository/run path",
                ):
                    _ = runner.run(
                        workspace,
                        harness_state,
                        opencode_logs,
                        control_dir,
                        run_state_file,
                        extra_env={"FORKLIFT_MAIN_BRANCH": "main"},
                    )

            popen_mock.assert_not_called()
            self.assertEqual(
                update_state.call_args_list[-1].kwargs.get("status"), "failed"
            )

    def test_dispatches_valid_rebase_events_to_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace, harness_state, opencode_logs, control_dir, run_state_file = (
                self._make_paths(root)
            )
            socket_path = control_dir / REBASE_EVENTS_SOCKET_NAME
            process = EventEmittingProcess(
                socket_path,
                [
                    b'{"v":1,"event":"progress","step":5,"total":31,"sha":"abc","subject":"Rename auth middleware"}\n',
                    b'{"v":1,"event":"conflict","step":5,"total":31,"files":["src/auth.py","tests/test_auth.py"]}\n',
                ],
            )
            events: list[RebaseEvent] = []
            runner = ContainerRunner(timeout_seconds=5)

            with (
                patch(
                    "forklift.container_runner.subprocess.Popen", return_value=process
                ),
                patch("forklift.container_runner.update_run_state"),
            ):
                _ = runner.run(
                    workspace,
                    harness_state,
                    opencode_logs,
                    control_dir,
                    run_state_file,
                    extra_env={"FORKLIFT_MAIN_BRANCH": "main"},
                    event_callback=events.append,
                )

            self.assertEqual(
                events,
                [
                    RebaseEvent(
                        event="progress",
                        step=5,
                        total=31,
                        sha="abc",
                        subject="Rename auth middleware",
                    ),
                    RebaseEvent(
                        event="conflict",
                        step=5,
                        total=31,
                        files=("src/auth.py", "tests/test_auth.py"),
                    ),
                ],
            )

    def test_warns_and_ignores_malformed_or_unknown_rebase_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace, harness_state, opencode_logs, control_dir, run_state_file = (
                self._make_paths(root)
            )
            socket_path = control_dir / REBASE_EVENTS_SOCKET_NAME
            process = EventEmittingProcess(
                socket_path,
                [
                    b"not-json\n",
                    b'{"v":2,"event":"progress","step":5,"total":31}\n',
                    b'{"v":1,"event":"mystery","step":5,"total":31}\n',
                ],
            )
            runner = ContainerRunner(timeout_seconds=5)

            with (
                patch(
                    "forklift.container_runner.subprocess.Popen", return_value=process
                ),
                patch("forklift.container_runner.update_run_state"),
                patch("forklift.container_runner.logger.warning") as warning_mock,
            ):
                _ = runner.run(
                    workspace,
                    harness_state,
                    opencode_logs,
                    control_dir,
                    run_state_file,
                    extra_env={"FORKLIFT_MAIN_BRANCH": "main"},
                    event_callback=lambda _event: self.fail("unexpected callback"),
                )

            warning_messages = [
                cast(str, call.args[0]) for call in warning_mock.call_args_list
            ]
            self.assertIn("Ignoring malformed rebase event payload", warning_messages)
            self.assertIn("Ignoring unknown rebase event version", warning_messages)
            self.assertIn("Ignoring unknown rebase event type", warning_messages)

    def test_updates_run_state_for_timeout_and_removes_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace, harness_state, opencode_logs, control_dir, run_state_file = (
                self._make_paths(root)
            )
            socket_path = control_dir / REBASE_EVENTS_SOCKET_NAME
            process = TimeoutProcess()
            runner = ContainerRunner(timeout_seconds=1)

            with (
                patch(
                    "forklift.container_runner.subprocess.Popen", return_value=process
                ),
                patch("forklift.container_runner.update_run_state") as update_state,
                patch.object(ContainerRunner, "_force_stop"),
            ):
                result = runner.run(
                    workspace,
                    harness_state,
                    opencode_logs,
                    control_dir,
                    run_state_file,
                    extra_env={"FORKLIFT_MAIN_BRANCH": "main"},
                )

            self.assertTrue(result.timed_out)
            statuses = [
                call.kwargs.get("status") for call in update_state.call_args_list
            ]
            self.assertEqual(statuses, ["running", "timed_out"])
            self.assertEqual(
                update_state.call_args_list[-1].kwargs.get("exit_code"), -9
            )
            self.assertFalse(socket_path.exists())

    def test_force_stop_prefers_graceful_docker_stop(self) -> None:
        runner = ContainerRunner(timeout_seconds=1)
        with patch("forklift.container_runner.subprocess.run") as run_mock:
            force_stop = cast(Callable[[str], None], getattr(runner, "_force_stop"))
            force_stop("forklift-test-container")

        run_mock.assert_called_once_with(
            [
                "docker",
                "stop",
                "--time",
                "10",
                "forklift-test-container",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )


if __name__ == "__main__":
    _ = unittest.main()
