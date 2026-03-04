from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forklift.cli import Forklift
from forklift.cli_authorship import OperatorIdentity
from forklift.container_runner import ContainerRunResult
from forklift.git import GitError, ResolvedUpstreamTarget, resolve_upstream_target
from forklift.opencode_env import OpenCodeEnv
from forklift.run_manager import RunPaths


class TargetPolicyUnitTests(unittest.TestCase):
    def _mock_tag_sha(self, _repo: Path, tag: str) -> str:
        """Return deterministic mock SHAs for prefixed vs bare equivalent tags."""

        if tag.startswith("v"):
            return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        return "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    def test_latest_version_policy_fails_when_no_supported_tags_exist(self) -> None:
        with patch("forklift.git.list_upstream_tag_commits", return_value={}):
            with self.assertRaisesRegex(GitError, "No upstream version tags found"):
                _ = resolve_upstream_target(
                    Path("."),
                    main_branch="main",
                    policy="latest-version",
                )

    def test_latest_version_policy_fails_on_ambiguous_equivalent_tags(self) -> None:
        with (
            patch(
                "forklift.git.list_upstream_tag_commits",
                return_value={
                    "v1.2.3": self._mock_tag_sha(Path("."), "v1.2.3"),
                    "1.2.3": self._mock_tag_sha(Path("."), "1.2.3"),
                },
            ),
        ):
            with self.assertRaisesRegex(GitError, "Ambiguous version tags"):
                _ = resolve_upstream_target(
                    Path("."),
                    main_branch="main",
                    policy="latest-version",
                )


class TargetPolicyGitIntegrationTests(unittest.TestCase):
    def _run_git(self, repo: Path, args: list[str]) -> str:
        """Run one git command in a test repository and return merged stdout/stderr."""

        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return (completed.stdout or "").strip()

    def _init_repo(self, repo: Path) -> None:
        """Initialize a commit-capable repository with deterministic local identity."""

        _ = self._run_git(repo, ["init", "-b", "main"])
        _ = self._run_git(repo, ["config", "user.name", "Forklift Tests"])
        _ = self._run_git(repo, ["config", "user.email", "tests@example.com"])
        _ = self._run_git(repo, ["remote", "add", "upstream", str(repo)])

    def _commit_file(self, repo: Path, relative_path: str, content: str, message: str) -> str:
        """Create one commit and return the resulting HEAD SHA."""

        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(content)
        _ = self._run_git(repo, ["add", relative_path])
        _ = self._run_git(repo, ["commit", "-m", message])
        return self._run_git(repo, ["rev-parse", "HEAD"])

    def test_latest_version_prefers_highest_semantic_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._init_repo(repo)
            _ = self._commit_file(repo, "base.txt", "base\n", "base")
            _ = self._run_git(repo, ["tag", "v1.2.9"])
            latest_sha = self._commit_file(repo, "next.txt", "next\n", "next")
            _ = self._run_git(repo, ["tag", "v1.2.10"])

            resolved = resolve_upstream_target(
                repo,
                main_branch="main",
                policy="latest-version",
            )

            self.assertEqual(resolved.policy, "latest-version")
            self.assertEqual(resolved.target_ref, "v1.2.10")
            self.assertEqual(resolved.target_sha, latest_sha)

    def test_latest_version_accepts_equivalent_prefixed_and_bare_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._init_repo(repo)
            tagged_sha = self._commit_file(repo, "release.txt", "release\n", "release")
            _ = self._run_git(repo, ["tag", "v2.0.0"])
            _ = self._run_git(repo, ["tag", "2.0.0"])

            resolved = resolve_upstream_target(
                repo,
                main_branch="main",
                policy="latest-version",
            )

            self.assertEqual(resolved.target_sha, tagged_sha)
            self.assertEqual(resolved.resolved_tag, "v2.0.0")

    def test_latest_version_rejects_ambiguous_equivalent_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._init_repo(repo)
            _ = self._commit_file(repo, "a.txt", "a\n", "a")
            _ = self._run_git(repo, ["tag", "v3.0.0"])
            _ = self._commit_file(repo, "b.txt", "b\n", "b")
            _ = self._run_git(repo, ["tag", "3.0.0"])

            with self.assertRaisesRegex(GitError, "Ambiguous version tags"):
                _ = resolve_upstream_target(
                    repo,
                    main_branch="main",
                    policy="latest-version",
                )

    def test_latest_version_rejects_when_only_prerelease_or_build_tags_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._init_repo(repo)
            _ = self._commit_file(repo, "rc.txt", "rc\n", "rc")
            _ = self._run_git(repo, ["tag", "v4.0.0-rc1"])
            _ = self._run_git(repo, ["tag", "4.0.0+build7"])

            with self.assertRaisesRegex(GitError, "No upstream version tags found"):
                _ = resolve_upstream_target(
                    repo,
                    main_branch="main",
                    policy="latest-version",
                )


class ForkliftPreRunIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _run_git(self, repo: Path, args: list[str]) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return (completed.stdout or "").strip()

    def _init_repo(self, repo: Path) -> str:
        _ = self._run_git(repo, ["init", "-b", "main"])
        _ = self._run_git(repo, ["config", "user.name", "Forklift Tests"])
        _ = self._run_git(repo, ["config", "user.email", "tests@example.com"])
        _ = (repo / "main.txt").write_text("main\n")
        _ = self._run_git(repo, ["add", "main.txt"])
        _ = self._run_git(repo, ["commit", "-m", "main"])
        return self._run_git(repo, ["rev-parse", "HEAD"])

    def _build_forklift(self, repo: Path) -> Forklift:
        forklift = Forklift()
        forklift.repo = repo
        forklift.main_branch = "main"
        forklift.target_policy = "latest-version"
        return forklift

    def _dummy_env(self) -> OpenCodeEnv:
        return OpenCodeEnv(
            api_key="api",
            model=None,
            variant="default",
            agent="worker",
            server_password="pw",
            server_port=4096,
        )

    def _run_paths(self, root: Path) -> RunPaths:
        run_dir = root / "run"
        workspace = run_dir / "workspace"
        harness_state = run_dir / "harness-state"
        opencode_logs = run_dir / "opencode-logs"
        workspace.mkdir(parents=True, exist_ok=True)
        harness_state.mkdir(parents=True, exist_ok=True)
        opencode_logs.mkdir(parents=True, exist_ok=True)
        return RunPaths(
            run_dir=run_dir,
            workspace=workspace,
            harness_state=harness_state,
            opencode_logs=opencode_logs,
            run_id="R123",
        )

    async def test_pre_run_noop_skips_run_directory_and_container(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            head_sha = self._init_repo(repo)
            forklift = self._build_forklift(repo)

            with (
                patch.object(
                    Forklift,
                    "_capture_operator_identity",
                    return_value=OperatorIdentity("Forklift Tests", "tests@example.com"),
                ),
                patch.object(Forklift, "_prepare_opencode_env", return_value=self._dummy_env()),
                patch.object(Forklift, "_resolve_chown_target", return_value=(1000, 1000)),
                patch.object(Forklift, "_discover_required_remotes", return_value={}),
                patch.object(Forklift, "_fetch_all", return_value=[]),
                patch.object(
                    Forklift,
                    "_resolve_upstream_target",
                    return_value=ResolvedUpstreamTarget(
                        policy="latest-version",
                        target_ref="v1.0.0",
                        target_sha=head_sha,
                        resolved_tag="v1.0.0",
                    ),
                ),
                patch("forklift.cli.RunDirectoryManager.prepare") as prepare_mock,
                patch("forklift.cli.ContainerRunner.run") as container_run_mock,
            ):
                await forklift.run()

            prepare_mock.assert_not_called()
            container_run_mock.assert_not_called()

    async def test_pre_run_non_noop_continues_to_prepare_and_container(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            _ = self._init_repo(repo)
            _ = self._run_git(repo, ["checkout", "-b", "feature"])
            _ = (repo / "feature.txt").write_text("feature\n")
            _ = self._run_git(repo, ["add", "feature.txt"])
            _ = self._run_git(repo, ["commit", "-m", "feature"])
            feature_sha = self._run_git(repo, ["rev-parse", "HEAD"])
            _ = self._run_git(repo, ["checkout", "main"])

            run_paths = self._run_paths(root)
            forklift = self._build_forklift(repo)

            with (
                patch.object(
                    Forklift,
                    "_capture_operator_identity",
                    return_value=OperatorIdentity("Forklift Tests", "tests@example.com"),
                ),
                patch.object(Forklift, "_prepare_opencode_env", return_value=self._dummy_env()),
                patch.object(Forklift, "_resolve_chown_target", return_value=(1000, 1000)),
                patch.object(Forklift, "_discover_required_remotes", return_value={}),
                patch.object(Forklift, "_fetch_all", return_value=[]),
                patch.object(
                    Forklift,
                    "_resolve_upstream_target",
                    return_value=ResolvedUpstreamTarget(
                        policy="latest-version",
                        target_ref="v2.0.0",
                        target_sha=feature_sha,
                        resolved_tag="v2.0.0",
                    ),
                ),
                patch.object(Forklift, "_build_container_env", return_value={}),
                patch.object(Forklift, "_chown_artifact", return_value=None),
                patch.object(Forklift, "_post_container_results", return_value=None),
                patch(
                    "forklift.cli.RunDirectoryManager.prepare",
                    return_value=run_paths,
                ) as prepare_mock,
                patch(
                    "forklift.cli.ContainerRunner.run",
                    return_value=ContainerRunResult(
                        exit_code=0,
                        timed_out=False,
                        stdout="",
                        stderr="",
                        container_name="forklift-test",
                    ),
                ) as container_run_mock,
                patch("forklift.cli.boxed", return_value="boxed output") as boxed_mock,
                patch("builtins.print") as print_mock,
            ):
                await forklift.run()

            prepare_mock.assert_called_once()
            container_run_mock.assert_called_once()
            boxed_mock.assert_called_once_with(
                "forklift clientlog run --follow",
                title="Client log tail command",
            )
            print_mock.assert_called_once_with("boxed output", flush=True)

    async def test_failed_container_logs_setup_details_before_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            _ = self._init_repo(repo)

            run_paths = self._run_paths(root)
            _ = (run_paths.harness_state / "setup.log").write_text(
                "== Setup Command ==\nbun install\n== Setup Output ==\nerror\n",
                encoding="utf-8",
            )
            forklift = self._build_forklift(repo)

            with (
                patch.object(
                    Forklift,
                    "_capture_operator_identity",
                    return_value=OperatorIdentity("Forklift Tests", "tests@example.com"),
                ),
                patch.object(Forklift, "_prepare_opencode_env", return_value=self._dummy_env()),
                patch.object(Forklift, "_resolve_chown_target", return_value=(1000, 1000)),
                patch.object(Forklift, "_discover_required_remotes", return_value={}),
                patch.object(Forklift, "_fetch_all", return_value=[]),
                patch.object(Forklift, "_is_target_already_integrated", return_value=False),
                patch.object(
                    Forklift,
                    "_resolve_upstream_target",
                    return_value=ResolvedUpstreamTarget(
                        policy="latest-version",
                        target_ref="v2.0.0",
                        target_sha="1234567890abcdef1234567890abcdef12345678",
                        resolved_tag="v2.0.0",
                    ),
                ),
                patch.object(Forklift, "_build_container_env", return_value={}),
                patch.object(Forklift, "_chown_artifact", return_value=None),
                patch(
                    "forklift.cli.RunDirectoryManager.prepare",
                    return_value=run_paths,
                ),
                patch(
                    "forklift.cli.ContainerRunner.run",
                    return_value=ContainerRunResult(
                        exit_code=1,
                        timed_out=False,
                        stdout="",
                        stderr="",
                        container_name="forklift-test",
                    ),
                ),
                patch.object(
                    Forklift,
                    "_log_setup_failure_details",
                    return_value=None,
                ) as setup_log_mock,
            ):
                with self.assertRaises(SystemExit) as ctx:
                    await forklift.run()

            self.assertEqual(ctx.exception.code, 1)
            setup_log_mock.assert_called_once_with(run_paths.harness_state)


if __name__ == "__main__":
    _ = unittest.main()
