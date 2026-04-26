from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from forklift.cli import Forklift
from forklift.first_command import First


class FirstCliParsingTests(unittest.TestCase):
    def test_forklift_parse_routes_first_subcommand(self) -> None:
        command = Forklift.parse(["first"])
        self.assertIsInstance(command.subcommand, First)

    def test_first_subcommand_does_not_invoke_orchestration_helpers(self) -> None:
        command = Forklift.parse(["first"])

        with (
            patch.object(First, "run", new=AsyncMock(return_value=None)) as first_run,
            patch.object(
                Forklift,
                "run",
                new=AsyncMock(
                    side_effect=AssertionError(
                        "Forklift.run should not execute for first subcommand"
                    )
                ),
            ),
            patch(
                "forklift.cli.RunDirectoryManager.prepare",
                side_effect=AssertionError("prepare should not run"),
            ),
            patch(
                "forklift.cli.ContainerRunner.run",
                side_effect=AssertionError("container should not run"),
            ),
            patch(
                "forklift.cli.post_container_results",
                side_effect=AssertionError("post-run should not run"),
            ),
            patch(
                "forklift.cli.rewrite_and_publish_local",
                side_effect=AssertionError("publish helper should not run"),
            ),
        ):
            error = command.start()

        self.assertIsNone(error)
        self.assertEqual(first_run.await_count, 1)


class FirstCommandTests(unittest.TestCase):
    def _init_repo(self) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
        temp_dir = tempfile.TemporaryDirectory()
        repo = Path(temp_dir.name) / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        _ = self._git(repo, "init", "-q", "-b", "main")
        _ = self._git(repo, "config", "user.name", "Test User")
        _ = self._git(repo, "config", "user.email", "test@example.com")
        return repo, temp_dir

    def _git(self, repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip()

    def _write(self, repo: Path, relative_path: str, content: str) -> None:
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(content, encoding="utf-8")

    def _commit_all(self, repo: Path, message: str) -> str:
        _ = self._git(repo, "add", "-A")
        _ = self._git(repo, "commit", "-q", "-m", message)
        return self._git(repo, "rev-parse", "HEAD")

    def _set_upstream_ref(self, repo: Path, target_ref: str) -> None:
        target_sha = self._git(repo, "rev-parse", target_ref)
        _ = self._git(repo, "update-ref", "refs/remotes/upstream/main", target_sha)

    def _run_command(self, command: First) -> tuple[str, str]:
        stdout_capture = StringIO()
        stderr_capture = StringIO()
        with (
            patch("forklift.git.logger.debug", return_value=None),
            redirect_stdout(stdout_capture),
            redirect_stderr(stderr_capture),
        ):
            asyncio.run(command.run())
        return stdout_capture.getvalue(), stderr_capture.getvalue()

    def test_first_prints_earliest_fork_only_commit_sha(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")
            self._set_upstream_ref(repo, "HEAD")

            self._write(repo, "fork.txt", "first\n")
            first_divergent_sha = self._commit_all(repo, "first divergent")
            self._write(repo, "fork.txt", "second\n")
            _ = self._commit_all(repo, "second divergent")

            command = First()
            command.repo = repo
            command.main_branch = "main"

            stdout, stderr = self._run_command(command)
        finally:
            temp_dir.cleanup()

        self.assertEqual(stdout, f"{first_divergent_sha}\n")
        self.assertEqual(stderr, "")

    def test_first_fails_when_upstream_ref_is_missing(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")

            command = First()
            command.repo = repo
            command.main_branch = "main"

            stdout_capture = StringIO()
            stderr_capture = StringIO()
            with (
                patch("forklift.git.logger.debug", return_value=None),
                redirect_stdout(stdout_capture),
                redirect_stderr(stderr_capture),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    asyncio.run(command.run())
        finally:
            temp_dir.cleanup()

        self.assertEqual(stdout_capture.getvalue(), "")
        self.assertEqual(stderr_capture.getvalue(), "")
        self.assertIn("first error:", str(ctx.exception.code))

    def test_first_fails_when_no_divergent_commits_exist(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")
            self._set_upstream_ref(repo, "HEAD")

            command = First()
            command.repo = repo
            command.main_branch = "main"

            stdout_capture = StringIO()
            stderr_capture = StringIO()
            with (
                patch("forklift.git.logger.debug", return_value=None),
                redirect_stdout(stdout_capture),
                redirect_stderr(stderr_capture),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    asyncio.run(command.run())
        finally:
            temp_dir.cleanup()

        self.assertEqual(stdout_capture.getvalue(), "")
        self.assertEqual(stderr_capture.getvalue(), "")
        self.assertEqual(str(ctx.exception.code), "first error: no divergent commits")

    def test_first_command_does_not_mutate_repository_state(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")
            self._set_upstream_ref(repo, "HEAD")

            self._write(repo, "fork-only.txt", "fork only\n")
            _ = self._commit_all(repo, "fork add")
            status_before = self._git(repo, "status", "--short")

            command = First()
            command.repo = repo
            command.main_branch = "main"

            _, _ = self._run_command(command)
            status_after = self._git(repo, "status", "--short")
        finally:
            temp_dir.cleanup()

        self.assertEqual(status_before, "")
        self.assertEqual(status_after, "")


if __name__ == "__main__":
    _ = unittest.main()
