from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from forklift.cli import Forklift
from forklift.git import GitError
from forklift.run_manager import RunPaths


class ForkliftTestHarness(Forklift):
    def rewrite_and_publish_local(
        self,
        repo_path: Path,
        run_paths: RunPaths,
        metadata: dict[str, object],
        target_branch: str,
        upstream_ref: str,
    ):
        return self._rewrite_and_publish_local(
            repo_path,
            run_paths,
            metadata,
            target_branch,
            upstream_ref,
        )

    def assert_no_agent_commits(self, workspace: Path, rewrite_range: str) -> None:
        self._assert_no_agent_commits(workspace, rewrite_range)


class ForkliftPostRunTests(unittest.TestCase):
    def _make_run_paths(self, root: Path) -> RunPaths:
        run_dir = root / "run"
        workspace = run_dir / "workspace"
        harness_state = run_dir / "harness-state"
        opencode_logs = run_dir / "opencode-logs"
        workspace.mkdir(parents=True)
        harness_state.mkdir(parents=True)
        opencode_logs.mkdir(parents=True)
        return RunPaths(
            run_dir=run_dir,
            workspace=workspace,
            harness_state=harness_state,
            opencode_logs=opencode_logs,
            run_id="ABCD",
        )

    def test_rewrite_uses_bounded_range_and_local_publication_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_paths = self._make_run_paths(root)
            repo_path = root / "local-repo"
            repo_path.mkdir()
            forklift = ForkliftTestHarness()

            metadata: dict[str, object] = {
                "operator_name": "Scott Hyndman",
                "operator_email": "scotty.hyndman@gmail.com",
                "created_at": "20260302_012207",
            }
            publication_branch = "upstream-merge/20260302T012207/main"
            upstream_sha = "1111111111111111111111111111111111111111"
            head_sha = "2222222222222222222222222222222222222222"

            def fake_run_git(repo: Path, args: list[str]) -> str:
                if args == ["checkout", publication_branch]:
                    self.assertEqual(repo, repo_path)
                    return "Switched to publication branch"
                self.assertEqual(repo, run_paths.workspace)
                if args == ["rev-parse", "upstream/main"]:
                    return upstream_sha
                if args == ["rev-parse", "HEAD"]:
                    return head_sha
                if args == ["rev-parse", "--verify", "upstream-main"]:
                    return upstream_sha
                if args == ["filter-repo", "--version"]:
                    return "a40bce548d2c"
                if args[0] == "filter-repo":
                    return ""
                if args[0] == "log":
                    return ""
                if args[0] == "push":
                    return "published"
                raise AssertionError(f"Unexpected git command: {args}")

            with (
                patch("forklift.cli.current_branch", return_value="main"),
                patch("forklift.cli.ensure_upstream_merged"),
                patch("forklift.cli.run_git", side_effect=fake_run_git) as run_git_mock,
                patch.object(Forklift, "_workspace_has_changes", return_value=False),
            ):
                result = forklift.rewrite_and_publish_local(
                    repo_path,
                    run_paths,
                    metadata,
                    "main",
                    "upstream/main",
                )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.rewritten)
            self.assertTrue(result.published)
            self.assertEqual(result.rewrite_range, "upstream-main..main")
            self.assertEqual(
                result.publication_branch,
                publication_branch,
            )

            filter_calls = [
                call
                for call in run_git_mock.call_args_list
                if call.args[1][0] == "filter-repo" and "--version" not in call.args[1]
            ]
            self.assertEqual(len(filter_calls), 1)
            filter_args = cast(list[str], filter_calls[0].args[1])
            self.assertIn("--refs=upstream-main..main", filter_args)

            push_calls = [
                call for call in run_git_mock.call_args_list if call.args[1][0] == "push"
            ]
            self.assertEqual(len(push_calls), 1)
            push_args = cast(list[str], push_calls[0].args[1])
            self.assertEqual(push_args[1], str(repo_path))
            self.assertEqual(
                push_args[2],
                f"main:{publication_branch}",
            )

            checkout_calls = [
                call
                for call in run_git_mock.call_args_list
                if cast(list[str], call.args[1]) == ["checkout", publication_branch]
            ]
            self.assertEqual(len(checkout_calls), 1)
            self.assertEqual(checkout_calls[0].args[0], repo_path)

    def test_rewrite_continues_when_publication_checkout_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_paths = self._make_run_paths(root)
            repo_path = root / "local-repo"
            repo_path.mkdir()
            forklift = ForkliftTestHarness()

            metadata: dict[str, object] = {
                "operator_name": "Scott Hyndman",
                "operator_email": "scotty.hyndman@gmail.com",
                "created_at": "20260302_012207",
            }
            publication_branch = "upstream-merge/20260302T012207/main"
            upstream_sha = "4444444444444444444444444444444444444444"
            head_sha = "5555555555555555555555555555555555555555"

            def fake_run_git(repo: Path, args: list[str]) -> str:
                if args == ["checkout", publication_branch]:
                    self.assertEqual(repo, repo_path)
                    raise GitError("local changes would be overwritten by checkout")
                self.assertEqual(repo, run_paths.workspace)
                if args == ["rev-parse", "upstream/main"]:
                    return upstream_sha
                if args == ["rev-parse", "HEAD"]:
                    return head_sha
                if args == ["rev-parse", "--verify", "upstream-main"]:
                    return upstream_sha
                if args == ["filter-repo", "--version"]:
                    return "a40bce548d2c"
                if args[0] == "filter-repo":
                    return ""
                if args[0] == "log":
                    return ""
                if args[0] == "push":
                    return "published"
                raise AssertionError(f"Unexpected git command: {args}")

            with (
                patch("forklift.cli.current_branch", return_value="main"),
                patch("forklift.cli.ensure_upstream_merged"),
                patch("forklift.cli.run_git", side_effect=fake_run_git) as run_git_mock,
                patch.object(Forklift, "_workspace_has_changes", return_value=False),
            ):
                result = forklift.rewrite_and_publish_local(
                    repo_path,
                    run_paths,
                    metadata,
                    "main",
                    "upstream/main",
                )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.rewritten)
            self.assertTrue(result.published)
            self.assertEqual(result.publication_branch, publication_branch)

            command_sequence = [
                (cast(Path, call.args[0]), cast(list[str], call.args[1]))
                for call in run_git_mock.call_args_list
            ]
            push_index = command_sequence.index(
                (
                    run_paths.workspace,
                    ["push", str(repo_path), f"main:{publication_branch}", "--force"],
                )
            )
            checkout_index = command_sequence.index(
                (repo_path, ["checkout", publication_branch])
            )
            self.assertLess(push_index, checkout_index)

    def test_rewrite_skips_when_head_matches_upstream_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_paths = self._make_run_paths(root)
            repo_path = root / "local-repo"
            repo_path.mkdir()
            forklift = ForkliftTestHarness()

            metadata: dict[str, object] = {
                "operator_name": "Scott Hyndman",
                "operator_email": "scotty.hyndman@gmail.com",
                "created_at": "20260302_012207",
            }
            anchor_sha = "3333333333333333333333333333333333333333"

            def fake_run_git(repo: Path, args: list[str]) -> str:
                self.assertEqual(repo, run_paths.workspace)
                if args == ["rev-parse", "upstream/main"]:
                    return anchor_sha
                if args == ["rev-parse", "HEAD"]:
                    return anchor_sha
                if args == ["rev-parse", "--verify", "upstream-main"]:
                    return anchor_sha
                raise AssertionError(f"Unexpected git command: {args}")

            with (
                patch("forklift.cli.current_branch", return_value="main"),
                patch("forklift.cli.run_git", side_effect=fake_run_git) as run_git_mock,
                patch.object(Forklift, "_workspace_has_changes", return_value=False),
            ):
                result = forklift.rewrite_and_publish_local(
                    repo_path,
                    run_paths,
                    metadata,
                    "main",
                    "upstream/main",
                )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertFalse(result.rewritten)
            self.assertFalse(result.published)
            self.assertIsNone(result.publication_branch)
            self.assertFalse(
                any(call.args[1][0] == "push" for call in run_git_mock.call_args_list)
            )
            self.assertFalse(
                any(
                    call.args[1][0] == "filter-repo"
                    for call in run_git_mock.call_args_list
                )
            )

    def test_assert_no_agent_commits_checks_only_requested_range(self) -> None:
        forklift = ForkliftTestHarness()
        workspace = Path("/tmp/workspace")

        with patch("forklift.cli.run_git", return_value="") as run_git_mock:
            forklift.assert_no_agent_commits(workspace, "upstream-main..main")

        logged_args = cast(list[str], run_git_mock.call_args.args[1])
        self.assertIn("upstream-main..main", logged_args)
        self.assertNotIn("--all", logged_args)


if __name__ == "__main__":
    _ = unittest.main()
