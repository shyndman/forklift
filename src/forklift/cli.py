from __future__ import annotations

import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import override

import structlog
from clypi import Command, arg
from clypi._cli import arg_parser
from libsh.logs import get_logger, setup_logging_from_env
from rich.traceback import install as install_rich_traceback

from .changelog import Changelog
from .cli_authorship import (
    AGENT_EMAIL as AUTHORSHIP_AGENT_EMAIL,
)
from .cli_authorship import (
    AGENT_NAME as AUTHORSHIP_AGENT_NAME,
)
from .cli_authorship import (
    FILTER_REPO_INSTALL_HELP as AUTHORSHIP_FILTER_REPO_INSTALL_HELP,
)
from .cli_authorship import (
    STASH_MESSAGE as AUTHORSHIP_STASH_MESSAGE,
)
from .cli_authorship import (
    OperatorIdentity,
    RewriteResult,
    assert_no_agent_commits,
    log_rewrite_summary,
    rewrite_and_publish_local,
    workspace_has_changes,
)
from .cli_post_run import (
    post_container_results,
)
from .cli_runtime import (
    DEFAULT_AGENT_LIFETIME,
    DEFAULT_TARGET_POLICY,
    apply_cli_overrides,
    build_container_env,
    chown_artifact,
    resolve_chown_target,
    resolved_agent_lifetime,
    resolved_effective_timeout_seconds,
    resolved_main_branch,
    resolved_target_policy,
)
from .container_runner import ContainerRunner
from .errors import (
    ContainerExitError,
    ContainerTimeoutError,
    ForkliftError,
    HarnessIncompleteError,
    RebaseStuckError,
    SetupError,
    UpstreamNotMergedError,
)
from .files_command import Files
from .first_command import First
from .git import (
    GitError,
    GitFetchResult,
    GitRemote,
    ResolvedUpstreamTarget,
    current_branch,
    discover_remotes,
    ensure_required_remotes,
    ensure_upstream_merged,
    fetch_remotes,
    is_ancestor,
    resolve_upstream_target,
    run_git,
)
from .logs import build_renderer
from .forklift_env import (
    DEFAULT_ENV_PATH,
    ForkliftEnv,
    ForkliftEnvError,
    load_forklift_env,
)
from .run_summary import build_run_summary, emit_run_summary
from .run_manager import RunDirectoryManager, RunPaths

setup_logging_from_env()
logger = get_logger()

AGENT_NAME = AUTHORSHIP_AGENT_NAME
AGENT_EMAIL = AUTHORSHIP_AGENT_EMAIL
STASH_MESSAGE = AUTHORSHIP_STASH_MESSAGE
FILTER_REPO_INSTALL_HELP = AUTHORSHIP_FILTER_REPO_INSTALL_HELP
HARNESS_STATUS_FILE_NAME = "harness-status.txt"
INSTRUCTION_OPTION = "--instruction"
INSTRUCTION_VALUE_HINT = "Use --instruction='...'."


def _default_instruction_list() -> list[str]:
    """Provide a strongly typed default for repeatable CLI instruction blocks."""

    return []


def exit_code_for(err: ForkliftError) -> int:
    """Map a domain run-lifecycle failure to the process exit code (CLI-owned)."""

    match err:
        case ContainerTimeoutError():
            return 2
        case UpstreamNotMergedError():
            return 3
        case RebaseStuckError():
            return 4
        case ContainerExitError():
            return err.container_exit_code
        case _:
            return 1


def outcome_label(err: ForkliftError) -> str:
    """Map a domain run-lifecycle failure to the terminal-footer outcome label."""

    match err:
        case ContainerTimeoutError():
            return "timed out"
        case RebaseStuckError():
            return "stuck"
        case _:
            return "failure"


class Forklift(Command):
    """Primary entrypoint for the Forklift host orchestrator."""

    subcommand: Changelog | Files | First | None = None
    repo: Path | str | None = None
    main_branch: str = arg(
        "main", help="Name of the primary branch to rebase (default: main)"
    )
    target_policy: str = arg(
        DEFAULT_TARGET_POLICY,
        help="Upstream target policy: 'tip' or 'latest-version' (default: latest-version)",
    )
    agent_lifetime: str = arg(
        DEFAULT_AGENT_LIFETIME,
        help="Agent lifetime: 'conflict' (fresh agent per conflict) or 'rebase' (one session) (default: conflict)",
    )
    debug: bool = arg(False, short="d", help="Enable debug logging")
    version: bool = arg(False, short="v", help="Print version and exit")
    model: str | None = arg(
        None,
        help="Override FORKLIFT_MODEL (e.g. 'openrouter:google/gemini-2.5-flash').",
    )
    forward_tz: bool = arg(False, help="Forward the host TZ variable into the sandbox")
    chown: str | None = arg(
        None,
        help="Reassign harness-state ownership to UID[:GID] after runs (defaults to $UID:$GID).",
    )
    timeout_seconds: int | None = arg(
        None,
        help="Override run timeout in seconds for this run.",
    )
    instruction: list[str] = arg(
        default_factory=_default_instruction_list,
        help="Extra instructions provided in addition to FORK.md contents. Repeat the flag to add more than one block.",
    )

    @override
    async def run(self) -> None:
        if self.version:
            self._print_version()
            return

        repo_path = self._resolve_repo_path()
        logger.info("Starting Forklift orchestration", repo=str(repo_path))
        run_manager = RunDirectoryManager()
        _ = run_manager.cleanup_expired_runs()

        run_paths: RunPaths | None = None
        run_duration_s = 0.0
        outcome = "success"
        exit_code = 0
        try:
            operator_identity = self._capture_operator_identity(repo_path)
            main_branch = self._resolved_main_branch()
            target_policy = self._resolved_target_policy()
            agent_lifetime = self._resolved_agent_lifetime()
            extra_instructions = self._validated_instructions()
            forklift_env = self._prepare_forklift_env()
            chown_uid, chown_gid = self._resolve_chown_target()

            remotes = self._discover_required_remotes(repo_path)
            fetch_results = self._fetch_all(repo_path, remotes)
            for result in fetch_results:
                if result.output:
                    logger.info(
                        "Fetch output", remote=result.name, output=result.output
                    )
                else:
                    logger.info("Fetch output", remote=result.name, status="up to date")
            logger.info("Remote discovery and fetch complete.")

            selected_target = self._resolve_upstream_target(
                repo_path,
                main_branch=main_branch,
                target_policy=target_policy,
            )
            logger.info(
                "Resolved upstream target",
                target_policy=selected_target.policy,
                target_ref=selected_target.target_ref,
                target_sha=selected_target.target_sha,
                target_tag=selected_target.resolved_tag,
            )

            if self._is_target_already_integrated(
                repo_path,
                target_sha=selected_target.target_sha,
                main_branch=main_branch,
            ):
                logger.info(
                    "Selected upstream target already integrated; skipping container run",
                    target_policy=selected_target.policy,
                    target_sha=selected_target.target_sha,
                    target_ref=selected_target.target_ref,
                )
                return

            run_paths = run_manager.prepare(
                repo_path,
                main_branch=main_branch,
                selected_upstream_sha=selected_target.target_sha,
                extra_metadata=self._metadata_overrides(
                    operator_identity, selected_target
                ),
                extra_instructions=extra_instructions,
            )
            _ = structlog.contextvars.bind_contextvars(run=run_paths.run_id)
            logger.info(
                "Run directory ready",
                run_dir=run_paths.run_dir,
                workspace=run_paths.workspace,
                harness_state=run_paths.harness_state,
                control_dir=run_paths.control_dir,
            )

            timeout_seconds = self._resolved_timeout_seconds(
                forklift_env.timeout_seconds
            )
            container_forklift_env = replace(
                forklift_env, timeout_seconds=timeout_seconds
            )
            run_started = time.monotonic()
            container_runner = ContainerRunner(timeout_seconds=timeout_seconds)
            container_result = container_runner.run(
                run_paths.workspace,
                run_paths.harness_state,
                run_paths.control_dir,
                run_paths.run_dir / "run-state.json",
                self._build_container_env(
                    container_forklift_env,
                    main_branch,
                    run_paths.run_id,
                    agent_lifetime,
                ),
            )
            run_duration_s = time.monotonic() - run_started

            self._chown_artifact(
                run_paths.harness_state, "harness-state", chown_uid, chown_gid
            )

            if container_result.timed_out:
                logger.error(
                    "Container timed out",
                    container=container_result.container_name,
                    timeout_seconds=container_runner.timeout_seconds,
                )
                raise ContainerTimeoutError()
            if container_result.exit_code != 0:
                logger.error(
                    "Container %s exited with code %s",
                    container_result.container_name,
                    container_result.exit_code,
                )
                raise ContainerExitError(container_result.exit_code)

            self._require_successful_harness_completion(run_paths.harness_state)
            logger.info("Container run completed successfully.")
            self._post_container_results(repo_path, run_paths, main_branch)
        except ForkliftError as err:
            outcome = outcome_label(err)
            exit_code = exit_code_for(err)

        if run_paths is not None:
            emit_run_summary(
                logger,
                build_run_summary(
                    run_paths.harness_state,
                    outcome=outcome,
                    duration_s=run_duration_s,
                ),
            )
        if exit_code != 0:
            raise SystemExit(exit_code)

    def _configure_logging(self) -> None:
        """Bootstrap structlog + Rich so every module shares contextual logs."""

        _ = install_rich_traceback(show_locals=True)
        level = logging.DEBUG if self.debug else logging.INFO
        renderer_processors, renderer = build_renderer(run_key="run")
        pre_chain = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            *renderer_processors,
        ]
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logging.basicConfig(level=level, handlers=[handler], force=True)
        structlog.configure(
            processors=[
                *pre_chain,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        structlog.contextvars.clear_contextvars()

    def _resolve_repo_path(self) -> Path:
        raw = self.repo
        base = Path.cwd() if raw is None else Path(raw)
        return base.expanduser().resolve()

    def _validated_instructions(self) -> tuple[str, ...]:
        """Reject empty run instructions while preserving caller-supplied text exactly."""

        validated: list[str] = []
        for instruction in self.instruction:
            if not instruction.strip():
                raise SystemExit(
                    f"{INSTRUCTION_OPTION} must not be empty or whitespace only."
                )
            validated.append(instruction)
        return tuple(validated)

    def _capture_operator_identity(self, repo_path: Path) -> OperatorIdentity:
        """Read and validate git operator identity used for run metadata."""

        try:
            name = run_git(repo_path, ["config", "--get", "user.name"]).strip()
        except GitError as exc:
            logger.exception(
                'Unable to read git user.name in %s; configure it via `git config --global user.name "Your Name"`.',
                repo_path,
            )
            raise SetupError("git user.name unreadable") from exc
        if not name:
            logger.error(
                'git user.name is empty in %s; set it via `git config --global user.name "Your Name"`.',
                repo_path,
            )
            raise SetupError("git user.name empty")

        try:
            email = run_git(repo_path, ["config", "--get", "user.email"]).strip()
        except GitError as exc:
            logger.exception(
                "Unable to read git user.email in %s; configure it via `git config --global user.email you@example.com`.",
                repo_path,
            )
            raise SetupError("git user.email unreadable") from exc
        if not email:
            logger.error(
                "git user.email is empty in %s; set it via `git config --global user.email you@example.com`.",
                repo_path,
            )
            raise SetupError("git user.email empty")

        logger.info("Captured operator identity", name=name, email=email)
        return OperatorIdentity(name=name, email=email)

    def _metadata_overrides(
        self,
        identity: OperatorIdentity,
        target: ResolvedUpstreamTarget,
    ) -> dict[str, object]:
        return {
            "operator_name": identity.name,
            "operator_email": identity.email,
            "target_policy": target.policy,
            "target_sha": target.target_sha,
            "target_tag": target.resolved_tag,
        }

    def _resolve_upstream_target(
        self,
        repo_path: Path,
        *,
        main_branch: str,
        target_policy: str,
    ) -> ResolvedUpstreamTarget:
        """Resolve and validate the upstream commit target selected for this run."""

        try:
            return resolve_upstream_target(
                repo_path,
                main_branch=main_branch,
                policy=target_policy,
            )
        except GitError as exc:
            logger.exception("Failed to resolve upstream target: %s", exc)
            raise SetupError("upstream target resolution failed") from exc

    def _is_target_already_integrated(
        self,
        repo_path: Path,
        *,
        target_sha: str,
        main_branch: str,
    ) -> bool:
        """Return whether selected upstream target is already merged into main branch."""

        try:
            return is_ancestor(repo_path, target_sha, main_branch)
        except GitError as exc:
            logger.exception(
                "Unable to determine whether %s is already merged into %s: %s",
                target_sha,
                main_branch,
                exc,
            )
            raise SetupError("ancestry check failed") from exc

    def _discover_required_remotes(self, repo_path: Path) -> dict[str, GitRemote]:
        try:
            remotes = ensure_required_remotes(repo_path)
        except GitError as exc:
            logger.exception("%s", exc)
            raise SetupError("remote discovery failed") from exc
        for remote in remotes.values():
            logger.info("Detected remote", name=remote.name, fetch_url=remote.fetch_url)
        return remotes

    def _fetch_all(
        self,
        repo_path: Path,
        remotes: dict[str, GitRemote],
    ) -> list[GitFetchResult]:
        try:
            return fetch_remotes(repo_path, remotes)
        except GitError as exc:
            logger.exception("%s", exc)
            raise SetupError("remote fetch failed") from exc

    def _post_container_results(
        self,
        repo_path: Path,
        run_paths: RunPaths,
        configured_branch: str,
    ) -> None:
        post_container_results(
            repo_path,
            run_paths,
            configured_branch,
            rewrite_and_publish_local_fn=self._rewrite_and_publish_local,
            log_rewrite_summary_fn=self._log_rewrite_summary,
            current_branch_fn=current_branch,
            ensure_upstream_merged_fn=ensure_upstream_merged,
        )

    def _require_successful_harness_completion(self, harness_state: Path) -> None:
        """Fail closed unless the harness explicitly reports successful completion."""

        status_path = harness_state / HARNESS_STATUS_FILE_NAME
        status = self._read_harness_status(status_path)
        if status.get("status") == "completed":
            return

        logger.error(
            "Harness did not report successful completion",
            path=status_path,
            status=status.get("status") or "missing",
            phase=status.get("phase"),
            message=status.get("message"),
        )
        raise HarnessIncompleteError("harness did not report successful completion")

    def _read_harness_status(self, status_path: Path) -> dict[str, str]:
        """Parse the harness completion marker written inside harness-state."""

        if not status_path.exists():
            return {}

        try:
            raw_status = status_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Unable to read harness status after run",
                path=status_path,
                error=str(exc),
            )
            return {}

        parsed: dict[str, str] = {}
        for line in raw_status.splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition("=")
            if not separator:
                logger.warning(
                    "Ignoring malformed harness status line",
                    path=status_path,
                    line=line,
                )
                continue
            parsed[key] = value
        return parsed

    def _workspace_has_changes(self, workspace: Path) -> bool:
        return workspace_has_changes(workspace)

    def _rewrite_and_publish_local(
        self,
        repo_path: Path,
        run_paths: RunPaths,
        metadata: dict[str, object],
        target_branch: str,
        upstream_ref: str,
    ) -> RewriteResult | None:
        return rewrite_and_publish_local(
            repo_path,
            run_paths,
            metadata,
            target_branch,
            upstream_ref,
            run_git_cmd=run_git,
            current_branch_fn=current_branch,
            ensure_upstream_merged_fn=ensure_upstream_merged,
            workspace_has_changes_fn=self._workspace_has_changes,
            discover_remotes_fn=discover_remotes,
        )

    def _assert_no_agent_commits(self, workspace: Path, rewrite_range: str) -> None:
        assert_no_agent_commits(workspace, rewrite_range, run_git_cmd=run_git)

    def _log_rewrite_summary(
        self, repo_path: Path, result: RewriteResult | None
    ) -> None:
        log_rewrite_summary(repo_path, result)

    def _prepare_forklift_env(self) -> ForkliftEnv:
        try:
            env = load_forklift_env(DEFAULT_ENV_PATH)
        except ForkliftEnvError as exc:
            logger.exception(
                "Failed to load Forklift config from %s: %s",
                DEFAULT_ENV_PATH,
                exc,
            )
            raise SetupError("Forklift env load failed") from exc
        logger.info("Loaded Forklift env", path=DEFAULT_ENV_PATH)
        logger.debug(
            "Forwarding Forklift configuration: model=%s effort=%s",
            env.model or "(default)",
            env.effort or "(default)",
        )
        return self._apply_cli_overrides(env)

    def _build_container_env(
        self,
        env: ForkliftEnv,
        main_branch: str,
        run_id: str,
        agent_lifetime: str,
    ) -> dict[str, str]:
        return build_container_env(
            env,
            main_branch,
            run_id,
            forward_tz=self.forward_tz,
            agent_lifetime=agent_lifetime,
        )

    def _apply_cli_overrides(self, env: ForkliftEnv) -> ForkliftEnv:
        return apply_cli_overrides(
            env,
            model=self.model,
        )

    def _resolved_main_branch(self) -> str:
        return resolved_main_branch(self.main_branch)

    def _resolved_target_policy(self) -> str:
        return resolved_target_policy(self.target_policy)

    def _resolved_agent_lifetime(self) -> str:
        return resolved_agent_lifetime(self.agent_lifetime)

    def _resolved_timeout_seconds(self, env_timeout_seconds: int | None) -> int:
        return resolved_effective_timeout_seconds(
            self.timeout_seconds, env_timeout_seconds
        )

    def _print_version(self) -> None:
        try:
            pkg_version = metadata.version("forklift")
        except metadata.PackageNotFoundError:
            pkg_version = "unknown"
        print(pkg_version)

    def _resolve_chown_target(self) -> tuple[int, int]:
        return resolve_chown_target(self.chown)

    def _chown_artifact(self, target: Path, label: str, uid: int, gid: int) -> None:
        chown_artifact(target, label=label, uid=uid, gid=gid)


def _extract_instruction_args(
    raw_args: Sequence[str],
    *,
    subcommands: set[str],
) -> tuple[list[str], list[str]]:
    """Collect repeatable `--instruction` values before Clypi sees the argv."""

    normalized_args = arg_parser.normalize_args(raw_args)
    filtered_args: list[str] = []
    instructions: list[str] = []
    index = 0
    while index < len(normalized_args):
        argument = normalized_args[index]
        if argument == "--":
            filtered_args.extend(normalized_args[index:])
            break

        parsed_argument = arg_parser.parse_as_attr(argument)
        if parsed_argument.is_pos() and parsed_argument.value in subcommands:
            filtered_args.extend(normalized_args[index:])
            break

        if argument != INSTRUCTION_OPTION:
            filtered_args.append(argument)
            index += 1
            continue

        next_index = index + 1
        if next_index >= len(normalized_args):
            raise SystemExit(
                f"{INSTRUCTION_OPTION} requires a value. {INSTRUCTION_VALUE_HINT}"
            )

        value = normalized_args[next_index]
        if value == "--" or arg_parser.parse_as_attr(value).is_opt():
            raise SystemExit(
                f"{INSTRUCTION_OPTION} requires a value. {INSTRUCTION_VALUE_HINT}"
            )

        instructions.append(value)
        index += 2

    return filtered_args, instructions


def parse_forklift_args(args: Sequence[str] | None = None) -> Forklift:
    """Parse CLI arguments while preserving repeatable `--instruction` order."""

    raw_args = list(args) if args is not None else sys.argv[1:]
    filtered_args, instructions = _extract_instruction_args(
        raw_args,
        subcommands={name for name in Forklift.subcommands() if name is not None},
    )
    command = Forklift.parse(filtered_args)
    command.instruction = instructions
    if command.subcommand is not None and instructions:
        raise SystemExit("--instruction is only valid for the main forklift run.")
    return command
