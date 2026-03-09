from __future__ import annotations

import os
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from typing import cast

from rich.console import Console

from forklift.cli import Forklift
from forklift.cli_authorship import OperatorIdentity
from forklift.cli_runtime import (
    HOST_GID_ENV,
    HOST_UID_ENV,
    build_container_env,
    resolve_chown_target,
    resolved_timeout_seconds,
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
        with (
            patch.dict(os.environ, {"TZ": "America/Vancouver"}, clear=False),
            patch("forklift.cli_runtime.default_host_ids", return_value=(321, 654)),
        ):
            container_env = build_container_env(
                env,
                "main",
                "run-123",
                forward_tz=True,
            )

        self.assertEqual(container_env["FORKLIFT_MAIN_BRANCH"], "main")
        self.assertEqual(container_env["FORKLIFT_RUN_ID"], "run-123")
        self.assertEqual(container_env[HOST_UID_ENV], "321")
        self.assertEqual(container_env[HOST_GID_ENV], "654")
        self.assertEqual(container_env["TZ"], "America/Vancouver")

    def test_resolved_target_policy_defaults_to_tip(self) -> None:
        self.assertEqual(resolved_target_policy(None), "tip")

    def test_resolved_target_policy_accepts_tip_and_latest_version(self) -> None:
        self.assertEqual(resolved_target_policy("tip"), "tip")
        self.assertEqual(resolved_target_policy("latest-version"), "latest-version")

    def test_resolved_target_policy_rejects_invalid_value(self) -> None:
        with self.assertRaises(SystemExit):
            _ = resolved_target_policy("bad-policy")

    def test_resolved_timeout_seconds_accepts_positive_values(self) -> None:
        self.assertEqual(resolved_timeout_seconds(12), 12)
        self.assertEqual(resolved_timeout_seconds("15"), 15)

    def test_resolved_timeout_seconds_rejects_non_positive_values(self) -> None:
        with self.assertRaises(SystemExit):
            _ = resolved_timeout_seconds(0)
        with self.assertRaises(SystemExit):
            _ = resolved_timeout_seconds(-1)

    def test_resolved_timeout_seconds_rejects_malformed_values(self) -> None:
        with self.assertRaises(SystemExit):
            _ = resolved_timeout_seconds("not-a-number")
        with self.assertRaises(SystemExit):
            _ = resolved_timeout_seconds(object())

    def test_forklift_parse_accepts_timeout_seconds_flag(self) -> None:
        command = Forklift.parse(["--timeout-seconds", "33"])
        self.assertEqual(command.timeout_seconds, 33)


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
        timeout_seconds: int | None = None,
    ) -> tuple[str, int | None, list[tuple[str, bool]], dict[str, object]]:
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

            class _ContainerRunnerStub:
                _result: ContainerRunResult
                timeout_seconds: int

                def __init__(self, result: ContainerRunResult, timeout: int | None) -> None:
                    self._result = result
                    self.timeout_seconds = timeout if timeout is not None else 600

                def run(self, *_args: object, **_kwargs: object) -> ContainerRunResult:
                    return self._result

            forklift = Forklift()
            forklift.repo = repo
            forklift.main_branch = "main"
            forklift.target_policy = "tip"
            forklift.timeout_seconds = timeout_seconds

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
                build_container_env_mock = stack.enter_context(
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
                container_runner_cls = stack.enter_context(patch("forklift.cli.ContainerRunner"))
                container_runner_cls.return_value = _ContainerRunnerStub(
                    container_result,
                    timeout_seconds,
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
                    constructor_kwargs = (
                        dict(container_runner_cls.call_args.kwargs)
                        if container_runner_cls.call_args is not None
                        else {}
                    )
                    if build_container_env_mock.call_args is not None:
                        env_arg = cast(object, build_container_env_mock.call_args.args[0])
                        if isinstance(env_arg, OpenCodeEnv):
                            constructor_kwargs["build_env_timeout_seconds"] = (
                                env_arg.timeout_seconds
                            )
                    return (
                        footer_output.getvalue(),
                        self._exit_code(exc.code),
                        logger_events,
                        constructor_kwargs,
                    )

            constructor_kwargs = (
                dict(container_runner_cls.call_args.kwargs)
                if container_runner_cls.call_args is not None
                else {}
            )
            if build_container_env_mock.call_args is not None:
                env_arg = cast(object, build_container_env_mock.call_args.args[0])
                if isinstance(env_arg, OpenCodeEnv):
                    constructor_kwargs["build_env_timeout_seconds"] = env_arg.timeout_seconds
            return footer_output.getvalue(), None, logger_events, constructor_kwargs

    def _exit_code(self, code: object) -> int:
        if isinstance(code, bool):
            return 1
        if isinstance(code, int):
            return code
        return 1

    async def test_footer_appears_for_success_and_failure(self) -> None:
        success_output, success_code, _, _ = await self._run_cli(
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

        failure_output, failure_code, _, _ = await self._run_cli(
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

    async def test_cli_timeout_override_is_forwarded_to_container_runner(self) -> None:
        _, exit_code, _, constructor_kwargs = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            timeout_seconds=37,
        )

        self.assertIsNone(exit_code)
        self.assertEqual(constructor_kwargs.get("timeout_seconds"), 37)
        self.assertEqual(constructor_kwargs.get("build_env_timeout_seconds"), 37)

    async def test_timeout_footer_keeps_exit_code_two(self) -> None:
        output, exit_code, _, _ = await self._run_cli(
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
        output, exit_code, logger_events, _ = await self._run_cli(
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
