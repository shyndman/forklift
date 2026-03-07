from __future__ import annotations

import os
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rich.console import Console

from forklift.cli import Forklift
from forklift.cli_authorship import OperatorIdentity
from forklift.cli_runtime import (
    build_container_env,
    resolve_chown_target,
    resolved_target_policy,
)
from forklift.container_runner import ContainerRunResult
from forklift.git import ResolvedUpstreamTarget
from forklift.opencode_env import OpenCodeEnv
from forklift.post_run_metrics import UsageSummary, render_usage_summary as real_render_usage_summary
from forklift.run_manager import RunPaths


class CliRuntimeHelperTests(unittest.TestCase):
    def test_resolve_chown_target_defaults_gid_when_omitted(self) -> None:
        with patch("forklift.cli_runtime.default_host_ids", return_value=(123, 456)):
            uid, gid = resolve_chown_target("42")
        self.assertEqual(uid, 42)
        self.assertEqual(gid, 456)

    def test_build_container_env_includes_required_keys(self) -> None:
        env = OpenCodeEnv(
            api_key="abc",
            model="model-x",
            variant="default",
            agent="worker",
            server_password="pw",
            server_port=4096,
        )
        with patch.dict(os.environ, {"TZ": "America/Vancouver"}, clear=False):
            container_env = build_container_env(
                env,
                "main",
                "run-123",
                forward_tz=True,
            )

        self.assertEqual(container_env["FORKLIFT_MAIN_BRANCH"], "main")
        self.assertEqual(container_env["FORKLIFT_RUN_ID"], "run-123")
        self.assertEqual(container_env["TZ"], "America/Vancouver")

    def test_resolved_target_policy_defaults_to_tip(self) -> None:
        self.assertEqual(resolved_target_policy(None), "tip")

    def test_resolved_target_policy_accepts_tip_and_latest_version(self) -> None:
        self.assertEqual(resolved_target_policy("tip"), "tip")
        self.assertEqual(resolved_target_policy("latest-version"), "latest-version")

    def test_resolved_target_policy_rejects_invalid_value(self) -> None:
        with self.assertRaises(SystemExit):
            _ = resolved_target_policy("bad-policy")


class CliRuntimeFooterIntegrationTests(unittest.IsolatedAsyncioTestCase):
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

    async def _run_cli(
        self,
        *,
        container_result: ContainerRunResult,
        post_run_side_effect: Exception | None = None,
        monitor_logger_after_footer: bool = False,
    ) -> tuple[str, int | None, list[tuple[str, bool]]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            run_paths = self._run_paths(root)
            footer_output = StringIO()
            logger_events: list[tuple[str, bool]] = []
            footer_started = False

            def log_event(method: str):
                def _record(*_args: object, **_kwargs: object) -> None:
                    logger_events.append((method, footer_started))

                return _record

            def render_with_marker(
                outcome: str,
                summary: UsageSummary,
                *,
                console: Console | None = None,
            ) -> None:
                nonlocal footer_started
                footer_started = True
                real_render_usage_summary(outcome, summary, console=console)

            forklift = Forklift()
            forklift.repo = repo
            forklift.main_branch = "main"
            forklift.target_policy = "tip"

            post_run_patch = patch.object(Forklift, "_post_container_results", return_value=None)
            if post_run_side_effect is not None:
                post_run_patch = patch.object(
                    Forklift,
                    "_post_container_results",
                    side_effect=post_run_side_effect,
                )

            logger_patchers = []
            if monitor_logger_after_footer:
                logger_patchers = [
                    patch("forklift.cli.logger.info", side_effect=log_event("info")),
                    patch("forklift.cli.logger.warning", side_effect=log_event("warning")),
                    patch("forklift.cli.logger.error", side_effect=log_event("error")),
                    patch("forklift.cli.logger.exception", side_effect=log_event("exception")),
                ]

            with ExitStack() as stack:
                _ = stack.enter_context(patch.object(Forklift, "_configure_logging", return_value=None))
                _ = stack.enter_context(
                    patch.object(
                        Forklift,
                        "_capture_operator_identity",
                        return_value=OperatorIdentity("Forklift Tests", "tests@example.com"),
                    )
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_prepare_opencode_env", return_value=self._dummy_env())
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_resolve_chown_target", return_value=(1000, 1000))
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_discover_required_remotes", return_value={})
                )
                _ = stack.enter_context(patch.object(Forklift, "_fetch_all", return_value=[]))
                _ = stack.enter_context(
                    patch.object(
                        Forklift,
                        "_resolve_upstream_target",
                        return_value=ResolvedUpstreamTarget(
                            policy="tip",
                            target_ref="upstream/main",
                            target_sha="1234567890abcdef1234567890abcdef12345678",
                            resolved_tag=None,
                        ),
                    )
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_is_target_already_integrated", return_value=False)
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_build_container_env", return_value={})
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_chown_artifact", return_value=None)
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_emit_clientlog_hint", return_value=None)
                )
                _ = stack.enter_context(
                    patch(
                        "forklift.cli.RunDirectoryManager.cleanup_expired_runs",
                        return_value=None,
                    )
                )
                _ = stack.enter_context(
                    patch(
                        "forklift.cli.RunDirectoryManager.prepare",
                        return_value=run_paths,
                    )
                )
                _ = stack.enter_context(
                    patch("forklift.cli.ContainerRunner.run", return_value=container_result)
                )
                _ = stack.enter_context(
                    patch(
                        "forklift.cli.parse_usage_summary",
                        return_value=UsageSummary.unavailable("no usage events found"),
                    )
                )
                _ = stack.enter_context(
                    patch("forklift.cli.render_usage_summary", side_effect=render_with_marker)
                )
                _ = stack.enter_context(
                    patch(
                        "forklift.cli.Console",
                        return_value=Console(
                            file=footer_output,
                            force_terminal=False,
                            color_system=None,
                            width=80,
                        ),
                    )
                )
                _ = stack.enter_context(post_run_patch)
                for logger_patcher in logger_patchers:
                    _ = stack.enter_context(logger_patcher)

                try:
                    await forklift.run()
                except SystemExit as exc:
                    return footer_output.getvalue(), self._exit_code(exc.code), logger_events

            return footer_output.getvalue(), None, logger_events

    def _exit_code(self, code: object) -> int:
        if isinstance(code, bool):
            return 1
        if isinstance(code, int):
            return code
        return 1

    async def test_footer_appears_for_success_and_failure(self) -> None:
        success_output, success_code, _ = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            )
        )
        self.assertIsNone(success_code)
        self.assertIn("Run complete: success", success_output)

        failure_output, failure_code, _ = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=9,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            )
        )
        self.assertEqual(failure_code, 9)
        self.assertIn("Run complete: failure", failure_output)

    async def test_timeout_footer_keeps_exit_code_two(self) -> None:
        output, exit_code, _ = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=-9,
                timed_out=True,
                stdout="",
                stderr="",
                container_name="forklift-test",
            )
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("Run complete: timed out", output)

    async def test_logger_calls_stop_after_footer_begins(self) -> None:
        output, exit_code, logger_events = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=5,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            monitor_logger_after_footer=True,
        )

        self.assertEqual(exit_code, 5)
        self.assertIn("Run complete: failure", output)
        self.assertTrue(logger_events)
        self.assertFalse(any(after_footer for _, after_footer in logger_events))


if __name__ == "__main__":
    _ = unittest.main()
