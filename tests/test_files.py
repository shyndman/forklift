from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from forklift.changelog_analysis import (
    parse_name_status_entries_output,
    parse_name_status_output,
)
from forklift.cli import Forklift
from forklift.files_command import Files


def lines(*rows: str) -> str:
    return "\n".join(rows)


class FilesCliParsingTests(unittest.TestCase):
    def test_forklift_parse_routes_files_subcommand(self) -> None:
        command = Forklift.parse(["files"])
        self.assertIsInstance(command.subcommand, Files)

    def test_files_subcommand_does_not_invoke_orchestration_helpers(self) -> None:
        command = Forklift.parse(["files"])

        with (
            patch.object(Files, "run", new=AsyncMock(return_value=None)) as files_run,
            patch.object(
                Forklift,
                "run",
                new=AsyncMock(
                    side_effect=AssertionError(
                        "Forklift.run should not execute for files subcommand"
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
        self.assertEqual(files_run.await_count, 1)


class CurrentPathParserTests(unittest.TestCase):
    def test_parse_name_status_entries_uses_current_paths_for_add_rename_copy(self) -> None:
        entries = parse_name_status_entries_output(
            lines(
                "A\talpha.py",
                "R100\told/name.py\tnew/name.py",
                "C100\tsrc/base.py\tfork/custom_base.py",
                "R087\tsrc/{old => new}/file.py",
            )
        )

        self.assertEqual(
            [(entry.path, entry.status) for entry in entries],
            [
                ("alpha.py", "A"),
                ("new/name.py", "R"),
                ("fork/custom_base.py", "C"),
                ("src/new/file.py", "R"),
            ],
        )
        self.assertEqual(
            parse_name_status_output(
                lines(
                    "A\talpha.py",
                    "R100\told/name.py\tnew/name.py",
                    "C100\tsrc/base.py\tfork/custom_base.py",
                )
            ),
            {
                "alpha.py": "A",
                "new/name.py": "R",
                "fork/custom_base.py": "C",
            },
        )


class FilesCommandTests(unittest.TestCase):
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
        return self._git(repo, "rev-parse", "--short", "HEAD")

    def _set_upstream_ref(self, repo: Path, target_ref: str) -> None:
        target_sha = self._git(repo, "rev-parse", target_ref)
        _ = self._git(repo, "update-ref", "refs/remotes/upstream/main", target_sha)

    def _run_command(self, command: Files) -> tuple[str, str]:
        stdout_capture = StringIO()
        stderr_capture = StringIO()
        with (
            patch("forklift.git.logger.debug", return_value=None),
            redirect_stdout(stdout_capture),
            redirect_stderr(stderr_capture),
        ):
            asyncio.run(command.run())
        return stdout_capture.getvalue(), stderr_capture.getvalue()

    def test_files_lists_alphabetized_fork_only_paths_and_ignores_working_tree(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")
            _ = self._git(repo, "branch", "upstream-source")

            self._write(repo, "zeta.txt", "fork only zeta\n")
            _ = self._commit_all(repo, "add zeta")
            self._write(repo, "alpha.txt", "fork only alpha\n")
            _ = self._commit_all(repo, "add alpha")
            self._write(repo, "shared.txt", "fork version\n")
            _ = self._commit_all(repo, "fork shared")
            self._write(repo, "working-tree-only.txt", "untracked\n")

            _ = self._git(repo, "checkout", "-q", "upstream-source")
            self._write(repo, "shared.txt", "upstream version\n")
            _ = self._commit_all(repo, "upstream shared")
            self._set_upstream_ref(repo, "HEAD")
            _ = self._git(repo, "checkout", "-q", "main")

            command = Files()
            command.repo = repo
            command.main_branch = "main"
            command.hash = False

            stdout, stderr = self._run_command(command)
        finally:
            temp_dir.cleanup()

        self.assertEqual(stdout, "alpha.txt\nzeta.txt\n")
        self.assertEqual(stderr, "")

    def test_files_hash_reports_first_current_path_commit_for_rename_and_copy(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "src/base.py", "base\n")
            self._write(repo, "src/old.py", "old\n")
            _ = self._commit_all(repo, "base")
            self._set_upstream_ref(repo, "HEAD")

            _ = (repo / "src/old.py").rename(repo / "fork-new.py")
            rename_short_sha = self._commit_all(repo, "rename old path")

            self._write(repo, "fork-copy.py", (repo / "src/base.py").read_text(encoding="utf-8"))
            copy_short_sha = self._commit_all(repo, "copy base path")

            self._write(repo, "fork-new.py", "old plus tweak\n")
            self._write(repo, "fork-copy.py", "base plus tweak\n")
            _ = self._commit_all(repo, "touch current paths")

            command = Files()
            command.repo = repo
            command.main_branch = "main"
            command.hash = True

            stdout, stderr = self._run_command(command)
        finally:
            temp_dir.cleanup()

        self.assertEqual(
            stdout,
            f"fork-copy.py\t{copy_short_sha}\nfork-new.py\t{rename_short_sha}\n",
        )
        self.assertEqual(stderr, "")

    def test_files_prints_empty_message_when_no_fork_owned_paths_exist(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")
            self._set_upstream_ref(repo, "HEAD")

            command = Files()
            command.repo = repo
            command.main_branch = "main"
            command.hash = False

            stdout, stderr = self._run_command(command)
        finally:
            temp_dir.cleanup()

        self.assertEqual(stdout, "No fork-owned files.\n")
        self.assertEqual(stderr, "")

    def test_files_fails_when_upstream_ref_is_missing(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")

            command = Files()
            command.repo = repo
            command.main_branch = "main"
            command.hash = False

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
        self.assertIn("files error:", str(ctx.exception.code))

    def test_files_command_does_not_mutate_repository_state(self) -> None:
        repo, temp_dir = self._init_repo()
        try:
            self._write(repo, "README.md", "base\n")
            _ = self._commit_all(repo, "base")
            self._set_upstream_ref(repo, "HEAD")

            self._write(repo, "fork-only.txt", "fork only\n")
            _ = self._commit_all(repo, "fork add")
            status_before = self._git(repo, "status", "--short")

            command = Files()
            command.repo = repo
            command.main_branch = "main"
            command.hash = True

            _, _ = self._run_command(command)
            status_after = self._git(repo, "status", "--short")
        finally:
            temp_dir.cleanup()

        self.assertEqual(status_before, "")
        self.assertEqual(status_after, "")


if __name__ == "__main__":
    _ = unittest.main()
