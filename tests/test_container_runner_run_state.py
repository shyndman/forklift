from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from forklift.container_runner import ContainerRunner


class SuccessfulProcess:
    returncode: int

    def __init__(self) -> None:
        self.returncode = 0

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        _ = timeout
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
    def _make_paths(self, root: Path) -> tuple[Path, Path, Path, Path]:
        workspace = root / "workspace"
        harness_state = root / "harness-state"
        opencode_logs = root / "opencode-logs"
        run_state_file = root / "run-state.json"
        workspace.mkdir(parents=True)
        harness_state.mkdir(parents=True)
        opencode_logs.mkdir(parents=True)
        return workspace, harness_state, opencode_logs, run_state_file

    def test_updates_run_state_for_successful_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace, harness_state, opencode_logs, run_state_file = self._make_paths(
                root
            )

            process = SuccessfulProcess()

            runner = ContainerRunner(timeout_seconds=5)

            with (
                patch(
                    "forklift.container_runner.subprocess.Popen", return_value=process
                ),
                patch("forklift.container_runner.update_run_state") as update_state,
            ):
                result = runner.run(
                    workspace,
                    harness_state,
                    opencode_logs,
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

    def test_updates_run_state_for_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace, harness_state, opencode_logs, run_state_file = self._make_paths(
                root
            )

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


if __name__ == "__main__":
    _ = unittest.main()
