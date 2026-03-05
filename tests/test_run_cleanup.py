from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil as stdlib_shutil
import tempfile
import unittest
from unittest.mock import patch

from forklift.run_manager import RUN_RETENTION_WINDOW_SECONDS, RunDirectoryManager


class RunCleanupTests(unittest.TestCase):
    def _set_directory_mtime(self, directory: Path, timestamp: float) -> None:
        os.utime(directory, (timestamp, timestamp))

    def test_cleanup_deletes_only_directories_older_than_one_week(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir)
            manager = RunDirectoryManager(runs_root=runs_root)
            now = datetime(2026, 3, 4, 20, 0, 0)
            now_epoch = now.timestamp()

            expired_dir = runs_root / "project_20260225_120000"
            boundary_dir = runs_root / "project_20260225_200000"
            recent_dir = runs_root / "project_20260227_200000"
            expired_dir.mkdir()
            boundary_dir.mkdir()
            recent_dir.mkdir()

            self._set_directory_mtime(expired_dir, now_epoch - RUN_RETENTION_WINDOW_SECONDS - 1)
            self._set_directory_mtime(boundary_dir, now_epoch - RUN_RETENTION_WINDOW_SECONDS)
            self._set_directory_mtime(recent_dir, now_epoch - RUN_RETENTION_WINDOW_SECONDS + 3600)

            result = manager.cleanup_expired_runs(now=now)

            self.assertFalse(expired_dir.exists())
            self.assertTrue(boundary_dir.exists())
            self.assertTrue(recent_dir.exists())
            self.assertEqual(result.scanned, 3)
            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.failed, 0)
            self.assertEqual(result.skipped, 2)

    def test_cleanup_logs_deletion_failures_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir)
            manager = RunDirectoryManager(runs_root=runs_root)
            now = datetime(2026, 3, 4, 20, 0, 0)
            now_epoch = now.timestamp()

            failing_dir = runs_root / "project_20260225_120000"
            deletable_dir = runs_root / "project_20260225_130000"
            failing_dir.mkdir()
            deletable_dir.mkdir()
            self._set_directory_mtime(failing_dir, now_epoch - RUN_RETENTION_WINDOW_SECONDS - 1)
            self._set_directory_mtime(deletable_dir, now_epoch - RUN_RETENTION_WINDOW_SECONDS - 1)

            original_rmtree = stdlib_shutil.rmtree

            def fake_rmtree(path: str | Path) -> None:
                if Path(path) == failing_dir:
                    raise PermissionError("denied")
                original_rmtree(path)

            with (
                patch("forklift.run_manager.shutil.rmtree", side_effect=fake_rmtree),
                patch("forklift.run_manager.logger.warning") as warning_mock,
            ):
                result = manager.cleanup_expired_runs(now=now)

            self.assertTrue(failing_dir.exists())
            self.assertFalse(deletable_dir.exists())
            self.assertEqual(result.scanned, 2)
            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.failed, 1)
            self.assertEqual(result.skipped, 0)
            warning_mock.assert_called()


if __name__ == "__main__":
    _ = unittest.main()
