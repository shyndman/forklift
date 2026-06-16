from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from typing import cast


from forklift.cli import Forklift, HARNESS_STATUS_FILE_NAME, parse_forklift_args
from forklift.cli import exit_code_for, outcome_label
from forklift.errors import (
    ContainerExitError,
    ContainerTimeoutError,
    HarnessIncompleteError,
    PublishError,
    RebaseStuckError,
    SetupError,
    UpstreamNotMergedError,
)
from forklift.cli_authorship import OperatorIdentity
from dataclasses import replace

from forklift.cli_runtime import (
    apply_cli_overrides,
    DEFAULT_TARGET_POLICY,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    HOST_GID_ENV,
    HOST_UID_ENV,
    build_container_env,
    resolve_chown_target,
    resolved_agent_lifetime,
    resolved_effective_timeout_seconds,
    resolved_timeout_seconds,
    resolved_target_policy,
)
from forklift.container_runner import ContainerRunResult
from forklift.git import ResolvedUpstreamTarget
from forklift.forklift_env import ForkliftEnv
from forklift.run_summary import RunSummary
from forklift.run_manager import RunPaths


HARNESS_COMPLETED_DURING_REBASE = (
    "status=completed\n"
    "phase=rebase\n"
    "message=Initial rebase completed cleanly; agent launch skipped\n"
)


class CliRuntimeHelperTests(unittest.TestCase):
    def test_resolve_chown_target_defaults_gid_when_omitted(self) -> None:
        with patch("forklift.cli_runtime.default_host_ids", return_value=(123, 456)):
            uid, gid = resolve_chown_target("42")
        self.assertEqual(uid, 42)
        self.assertEqual(gid, 456)

    def test_build_container_env_includes_required_keys(self) -> None:
        env = ForkliftEnv(
            model="model-x",
            effort=None,
            timeout_seconds=None,
            openrouter_api_key="api",
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
                agent_lifetime="conflict",
            )

        self.assertEqual(container_env["FORKLIFT_MAIN_BRANCH"], "main")
        self.assertEqual(container_env["FORKLIFT_RUN_ID"], "run-123")
        self.assertEqual(container_env["FORKLIFT_AGENT_LIFETIME"], "conflict")
        self.assertEqual(container_env[HOST_UID_ENV], "321")
        self.assertEqual(container_env[HOST_GID_ENV], "654")
        self.assertEqual(container_env["TZ"], "America/Vancouver")

    def test_resolved_target_policy_defaults_to_latest_version(self) -> None:
        self.assertEqual(resolved_target_policy(None), DEFAULT_TARGET_POLICY)
        self.assertEqual(DEFAULT_TARGET_POLICY, "latest-version")

    def test_resolved_agent_lifetime_defaults_to_conflict(self) -> None:
        self.assertEqual(resolved_agent_lifetime(None), "conflict")

    def test_resolved_agent_lifetime_accepts_rebase(self) -> None:
        self.assertEqual(resolved_agent_lifetime("rebase"), "rebase")

    def test_resolved_agent_lifetime_rejects_unknown(self) -> None:
        with self.assertRaises(SystemExit):
            _ = resolved_agent_lifetime("forever")

    def test_forklift_defaults_target_policy_to_latest_version(self) -> None:
        command = Forklift.parse([])

        self.assertEqual(command.target_policy, DEFAULT_TARGET_POLICY)

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
        with self.assertRaises(SystemExit):
            _ = resolved_timeout_seconds(True)

    def test_resolved_effective_timeout_seconds_precedence(self) -> None:
        self.assertEqual(resolved_effective_timeout_seconds(30, 45), 30)
        self.assertEqual(resolved_effective_timeout_seconds(None, 45), 45)
        self.assertEqual(
            resolved_effective_timeout_seconds(None, None),
            DEFAULT_RUN_TIMEOUT_SECONDS,
        )

    def test_forklift_parse_accepts_timeout_seconds_flag(self) -> None:
        command = parse_forklift_args(["--timeout-seconds", "33"])
        self.assertEqual(command.timeout_seconds, 33)

    def test_forklift_parse_collects_repeated_instruction_flags(self) -> None:
        command = parse_forklift_args(
            [
                "--instruction",
                "Resolve package-lock.json using upstream",
                "--instruction",
                "Keep fork-only telemetry hooks",
            ]
        )

        self.assertEqual(
            command.instruction,
            [
                "Resolve package-lock.json using upstream",
                "Keep fork-only telemetry hooks",
            ],
        )

    def test_instruction_flag_is_rejected_for_subcommands(self) -> None:
        with self.assertRaises(SystemExit):
            _ = parse_forklift_args(["--instruction", "Resolve conflict", "changelog"])


class CliRuntimeFooterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _dummy_env(self, *, timeout_seconds: int | None = None) -> ForkliftEnv:
        return ForkliftEnv(
            model=None,
            effort=None,
            timeout_seconds=timeout_seconds,
            openrouter_api_key="api",
        )

    def _run_paths(self, root: Path) -> RunPaths:
        run_dir = root / "run"
        workspace = run_dir / "workspace"
        harness_state = run_dir / "harness-state"
        control_dir = run_dir / "control"
        workspace.mkdir(parents=True, exist_ok=True)
        harness_state.mkdir(parents=True, exist_ok=True)
        control_dir.mkdir(parents=True, exist_ok=True)
        return RunPaths(
            run_dir=run_dir,
            workspace=workspace,
            harness_state=harness_state,
            control_dir=control_dir,
            run_id="R123",
        )

    async def _run_cli(
        self,
        *,
        container_result: ContainerRunResult,
        instructions: list[str] | None = None,
        post_run_side_effect: Exception | None = None,
        monitor_logger_after_footer: bool = False,
        timeout_seconds: int | None = None,
        env_timeout_seconds: int | None = None,
        harness_status_content: str | None = None,
    ) -> tuple[str, int | None, list[tuple[str, bool, str | None]], dict[str, object]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            run_paths = self._run_paths(root)
            if harness_status_content is not None:
                _ = (run_paths.harness_state / HARNESS_STATUS_FILE_NAME).write_text(
                    harness_status_content,
                    encoding="utf-8",
                )
            captured_outcome: str | None = None
            logger_events: list[tuple[str, bool, str | None]] = []
            footer_started = False

            def log_event(method: str):
                def _record(*args: object, **_kwargs: object) -> None:
                    message = args[0] if args and isinstance(args[0], str) else None
                    logger_events.append((method, footer_started, message))

                return _record

            def capture_summary(_logger: object, summary: RunSummary) -> None:
                nonlocal footer_started, captured_outcome
                footer_started = True
                captured_outcome = summary.outcome

            class _ContainerRunnerStub:
                _result: ContainerRunResult
                timeout_seconds: int

                def __init__(
                    self,
                    result: ContainerRunResult,
                    timeout: int | None,
                ) -> None:
                    self._result = result
                    self.timeout_seconds = (
                        timeout if timeout is not None else DEFAULT_RUN_TIMEOUT_SECONDS
                    )

                def run(self, *_args: object, **_kwargs: object) -> ContainerRunResult:
                    return self._result

            forklift = Forklift()
            forklift.repo = repo
            forklift.main_branch = "main"
            forklift.target_policy = "tip"
            forklift.timeout_seconds = timeout_seconds
            forklift.instruction = list(instructions or [])

            post_run_patch = patch.object(
                Forklift, "_post_container_results", return_value=None
            )
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
                    patch(
                        "forklift.cli.logger.warning", side_effect=log_event("warning")
                    ),
                    patch("forklift.cli.logger.error", side_effect=log_event("error")),
                    patch(
                        "forklift.cli.logger.exception",
                        side_effect=log_event("exception"),
                    ),
                ]

            with ExitStack() as stack:
                _ = stack.enter_context(
                    patch.object(Forklift, "_configure_logging", return_value=None)
                )
                _ = stack.enter_context(
                    patch.object(
                        Forklift,
                        "_capture_operator_identity",
                        return_value=OperatorIdentity(
                            "Forklift Tests", "tests@example.com"
                        ),
                    )
                )
                _ = stack.enter_context(
                    patch.object(
                        Forklift,
                        "_prepare_forklift_env",
                        return_value=self._dummy_env(
                            timeout_seconds=env_timeout_seconds
                        ),
                    )
                )
                _ = stack.enter_context(
                    patch.object(
                        Forklift, "_resolve_chown_target", return_value=(1000, 1000)
                    )
                )
                _ = stack.enter_context(
                    patch.object(
                        Forklift, "_discover_required_remotes", return_value={}
                    )
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_fetch_all", return_value=[])
                )
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
                    patch.object(
                        Forklift, "_is_target_already_integrated", return_value=False
                    )
                )
                build_container_env_mock = stack.enter_context(
                    patch.object(Forklift, "_build_container_env", return_value={})
                )
                _ = stack.enter_context(
                    patch.object(Forklift, "_chown_artifact", return_value=None)
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
                container_runner_cls = stack.enter_context(
                    patch("forklift.cli.ContainerRunner")
                )
                effective_timeout = (
                    timeout_seconds
                    if timeout_seconds is not None
                    else env_timeout_seconds
                    if env_timeout_seconds is not None
                    else DEFAULT_RUN_TIMEOUT_SECONDS
                )
                container_runner_cls.return_value = _ContainerRunnerStub(
                    container_result,
                    effective_timeout,
                )
                _ = stack.enter_context(
                    patch(
                        "forklift.cli.emit_run_summary",
                        side_effect=capture_summary,
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
                        env_arg = cast(
                            object, build_container_env_mock.call_args.args[0]
                        )
                        if isinstance(env_arg, ForkliftEnv):
                            constructor_kwargs["build_env_timeout_seconds"] = (
                                env_arg.timeout_seconds
                            )
                    return (
                        captured_outcome or "",
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
                if isinstance(env_arg, ForkliftEnv):
                    constructor_kwargs["build_env_timeout_seconds"] = (
                        env_arg.timeout_seconds
                    )
            return captured_outcome or "", None, logger_events, constructor_kwargs

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
            ),
            harness_status_content=HARNESS_COMPLETED_DURING_REBASE,
        )
        self.assertIsNone(success_code)
        self.assertEqual(success_output, "success")

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
        self.assertEqual(failure_output, "failure")

    async def test_cli_timeout_override_is_forwarded_to_container_runner(self) -> None:
        _, exit_code, _, constructor_kwargs = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            harness_status_content=HARNESS_COMPLETED_DURING_REBASE,
            timeout_seconds=37,
        )

        self.assertIsNone(exit_code)
        self.assertEqual(constructor_kwargs.get("timeout_seconds"), 37)
        self.assertEqual(constructor_kwargs.get("build_env_timeout_seconds"), 37)

    async def test_forklift_env_timeout_is_used_when_cli_timeout_missing(self) -> None:
        _, exit_code, _, constructor_kwargs = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            harness_status_content=HARNESS_COMPLETED_DURING_REBASE,
            env_timeout_seconds=425,
        )

        self.assertIsNone(exit_code)
        self.assertEqual(constructor_kwargs.get("timeout_seconds"), 425)
        self.assertEqual(constructor_kwargs.get("build_env_timeout_seconds"), 425)

    async def test_default_timeout_is_used_when_cli_and_env_missing(self) -> None:
        _, exit_code, _, constructor_kwargs = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            harness_status_content=HARNESS_COMPLETED_DURING_REBASE,
        )

        self.assertIsNone(exit_code)
        self.assertEqual(
            constructor_kwargs.get("timeout_seconds"),
            DEFAULT_RUN_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            constructor_kwargs.get("build_env_timeout_seconds"),
            DEFAULT_RUN_TIMEOUT_SECONDS,
        )

    async def test_whitespace_only_instruction_fails_before_run_preparation(
        self,
    ) -> None:
        _, exit_code, _, constructor_kwargs = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            instructions=["   "],
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(constructor_kwargs, {})

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
        self.assertEqual(output, "timed out")

    async def test_missing_harness_completion_marker_fails_closed(self) -> None:
        output, exit_code, _, _ = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            post_run_side_effect=AssertionError("post-run verification should not run"),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(output, "failure")

    async def test_failed_harness_marker_blocks_post_run_verification(self) -> None:
        output, exit_code, _, _ = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="",
                stderr="",
                container_name="forklift-test",
            ),
            harness_status_content="status=failed\nphase=setup\nmessage=Setup command failed before agent launch\n",
            post_run_side_effect=AssertionError("post-run verification should not run"),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(output, "failure")

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
        self.assertEqual(output, "failure")
        self.assertTrue(logger_events)
        self.assertFalse(any(after_footer for _, after_footer, _ in logger_events))

    async def test_container_streams_are_not_logged_after_exit(self) -> None:
        _, exit_code, logger_events, _ = await self._run_cli(
            container_result=ContainerRunResult(
                exit_code=0,
                timed_out=False,
                stdout="container stdout should stay hidden",
                stderr="container stderr should stay hidden",
                container_name="forklift-test",
            ),
            monitor_logger_after_footer=True,
            harness_status_content=HARNESS_COMPLETED_DURING_REBASE,
        )

        self.assertIsNone(exit_code)
        logged_messages = [message for _, _, message in logger_events]
        self.assertNotIn("Container stdout", logged_messages)
        self.assertNotIn("Container stderr", logged_messages)


class ExitCodeMappingTests(unittest.TestCase):
    def test_exit_code_for_each_error(self) -> None:
        self.assertEqual(exit_code_for(ContainerTimeoutError()), 2)
        self.assertEqual(exit_code_for(UpstreamNotMergedError()), 3)
        self.assertEqual(exit_code_for(RebaseStuckError()), 4)
        self.assertEqual(exit_code_for(ContainerExitError(137)), 137)
        self.assertEqual(exit_code_for(SetupError()), 1)
        self.assertEqual(exit_code_for(HarnessIncompleteError()), 1)
        self.assertEqual(exit_code_for(PublishError()), 1)

    def test_outcome_label_for_each_error(self) -> None:
        self.assertEqual(outcome_label(ContainerTimeoutError()), "timed out")
        self.assertEqual(outcome_label(RebaseStuckError()), "stuck")
        self.assertEqual(outcome_label(ContainerExitError(5)), "failure")


class ApplyCliOverridesTests(unittest.TestCase):
    def _base_env(self) -> ForkliftEnv:
        return ForkliftEnv(
            model=None,
            effort=None,
            timeout_seconds=None,
            gemini_api_key="test-key",
        )

    def test_provider_prefixed_model_override_is_accepted(self) -> None:
        # Model ids are ``provider:model``; the colon must pass validation.
        result = apply_cli_overrides(self._base_env(), model="google:gemini-2.5-flash")
        self.assertEqual(result.model, "google:gemini-2.5-flash")

    def test_none_override_preserves_configured_model(self) -> None:
        env = replace(
            self._base_env(), model="openrouter:google/gemini-3-flash-preview"
        )
        result = apply_cli_overrides(env, model=None)
        self.assertEqual(result.model, "openrouter:google/gemini-3-flash-preview")

    def test_model_override_with_illegal_characters_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            _ = apply_cli_overrides(self._base_env(), model="bad model id")


if __name__ == "__main__":
    _ = unittest.main()
