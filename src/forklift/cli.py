from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import logging
from importlib import metadata
from pathlib import Path
from shlex import quote as shell_quote
import sys
from typing import override

from clypi import Command, arg, boxed
from clypi._cli import arg_parser
import structlog
from rich.traceback import install as install_rich_traceback
from rich.console import Console

from .cli_authorship import (
    AGENT_EMAIL as AUTHORSHIP_AGENT_EMAIL,
    AGENT_NAME as AUTHORSHIP_AGENT_NAME,
    FILTER_REPO_INSTALL_HELP as AUTHORSHIP_FILTER_REPO_INSTALL_HELP,
    STASH_MESSAGE as AUTHORSHIP_STASH_MESSAGE,
    OperatorIdentity,
    RewriteResult,
    assert_no_agent_commits,
    log_rewrite_summary,
    rewrite_and_publish_local,
    workspace_has_changes,
)
from .cli_post_run import (
    STUCK_EXIT_CODE as POST_RUN_STUCK_EXIT_CODE,
    post_container_results,
)
from .post_run_metrics import (
    parse_usage_summary,
    render_completion_report,
    render_usage_summary,
)
from .cli_runtime import (
    apply_cli_overrides,
    build_container_env,
    chown_artifact,
    resolve_chown_target,
    resolved_effective_timeout_seconds,
    resolved_main_branch,
    resolved_target_policy,
)
from .changelog import Changelog
from .first_command import First
from .files_command import Files
from .clientlog import Clientlog
from .container_runner import ContainerRunner, RebaseEvent
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
from libsh.logs import setup_logging_from_env, get_logger
from .logs import build_renderer
from .opencode_env import (
    DEFAULT_ENV_PATH,
    OpenCodeEnv,
    OpenCodeEnvError,
    load_opencode_env,
)
from .run_manager import RunDirectoryManager, RunPaths

setup_logging_from_env()
logger = get_logger()

AGENT_NAME = AUTHORSHIP_AGENT_NAME
AGENT_EMAIL = AUTHORSHIP_AGENT_EMAIL
STASH_MESSAGE = AUTHORSHIP_STASH_MESSAGE
FILTER_REPO_INSTALL_HELP = AUTHORSHIP_FILTER_REPO_INSTALL_HELP
STUCK_EXIT_CODE = POST_RUN_STUCK_EXIT_CODE
HARNESS_STATUS_FILE_NAME = "harness-status.txt"
CLIENT_LOG_TAIL_LINES = 120
CLIENTLOG_HINT_TITLE = "Client log tail command"
CLIENTLOG_HINT_TEMPLATE = "forklift clientlog {run_dir_name} --follow"
INSTRUCTION_OPTION = "--instruction"
INSTRUCTION_VALUE_HINT = "Use --instruction='...'."


def _default_instruction_list() -> list[str]:
    """Provide a strongly typed default for repeatable CLI instruction blocks."""

    return []


class Forklift(Command):
    """Primary entrypoint for the Forklift host orchestrator."""

    subcommand: Changelog | Clientlog | Files | First | None = None
    repo: Path | str | None = None
    main_branch: str = arg(
        "main", help="Name of the primary branch to rebase (default: main)"
    )
    target_policy: str = arg(
        "tip",
        help="Upstream target policy: 'tip' or 'latest-version' (default: tip)",
    )
    debug: bool = arg(False, short="d", help="Enable debug logging")
    version: bool = arg(False, short="v", help="Print version and exit")
    model: str | None = arg(
        None, help="Override OPENCODE_MODEL (letters, numbers, punctuation ._-/)."
    )
    variant: str | None = arg(
        None, help="Override OPENCODE_VARIANT (letters, numbers, punctuation ._-/)."
    )
    agent: str | None = arg(
        None, help="Override OPENCODE_AGENT (letters, numbers, punctuation ._-/)."
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

        operator_identity = self._capture_operator_identity(repo_path)
        main_branch = self._resolved_main_branch()
        target_policy = self._resolved_target_policy()
        extra_instructions = self._validated_instructions()
        opencode_env = self._prepare_opencode_env()
        chown_uid, chown_gid = self._resolve_chown_target()

        remotes = self._discover_required_remotes(repo_path)
        fetch_results = self._fetch_all(repo_path, remotes)
        for result in fetch_results:
            if result.output:
                logger.info("Fetch output", remote=result.name, output=result.output)
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
            extra_metadata=self._metadata_overrides(operator_identity, selected_target),
            extra_instructions=extra_instructions,
        )
        _ = structlog.contextvars.bind_contextvars(run=run_paths.run_id)
        logger.info(
            "Run directory ready",
            run_dir=run_paths.run_dir,
            workspace=run_paths.workspace,
            harness_state=run_paths.harness_state,
            opencode_logs=run_paths.opencode_logs,
            control_dir=run_paths.control_dir,
        )
        self._emit_clientlog_hint(run_paths.run_dir.name)

        timeout_seconds = self._resolved_timeout_seconds(opencode_env.timeout_seconds)
        container_opencode_env = replace(opencode_env, timeout_seconds=timeout_seconds)
        container_runner = ContainerRunner(timeout_seconds=timeout_seconds)
        container_result = container_runner.run(
            run_paths.workspace,
            run_paths.harness_state,
            run_paths.opencode_logs,
            run_paths.control_dir,
            run_paths.run_dir / "run-state.json",
            self._build_container_env(
                container_opencode_env, main_branch, run_paths.run_id
            ),
            event_callback=self._log_rebase_event,
        )

        agent_log_path = run_paths.harness_state / "opencode-client.log"
        if agent_log_path.exists():
            logger.info("Agent log transcript available", path=agent_log_path)
        else:
            logger.warning(
                "Agent log transcript missing; harness may not have emitted logs.",
                path=agent_log_path,
            )

        self._chown_artifact(
            run_paths.harness_state, "harness-state", chown_uid, chown_gid
        )
        self._chown_artifact(
            run_paths.opencode_logs, "opencode-logs", chown_uid, chown_gid
        )

        if container_result.stdout.strip():
            logger.info("Container stdout", stdout=container_result.stdout.strip())
        if container_result.stderr.strip():
            logger.info("Container stderr", stderr=container_result.stderr.strip())
        outcome = "success"
        exit_code = 0
        try:
            if container_result.timed_out:
                logger.error(
                    "Container timed out",
                    container=container_result.container_name,
                    timeout_seconds=container_runner.timeout_seconds,
                )
                outcome = "timed out"
                exit_code = 2
            elif container_result.exit_code != 0:
                logger.error(
                    "Container %s exited with code %s",
                    container_result.container_name,
                    container_result.exit_code,
                )
                outcome = "failure"
                exit_code = container_result.exit_code
            else:
                self._require_successful_harness_completion(run_paths.harness_state)
                logger.info("Container run completed successfully.")
                self._post_container_results(repo_path, run_paths, main_branch)
        except SystemExit as exc:
            if exc.code == STUCK_EXIT_CODE:
                outcome = "stuck"
                exit_code = STUCK_EXIT_CODE
            else:
                outcome = "failure"
                exit_code = self._resolved_exit_code(exc.code)

        self._render_terminal_summary(
            outcome,
            agent_log_path,
            run_paths.workspace,
            run_paths.harness_state,
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

    def _log_rebase_event(self, event: RebaseEvent) -> None:
        fields: dict[str, object] = {"step": event.step, "total": event.total}
        if event.sha is not None:
            fields["sha"] = event.sha
        if event.subject is not None:
            fields["subject"] = event.subject
        if event.files:
            fields["files"] = ", ".join(event.files)

        match event.event:
            case "progress":
                logger.info(f"Rebase {event.step}/{event.total}", **fields)
            case "conflict":
                logger.warning(
                    f"Conflict {event.step}/{event.total}",
                    conflict_files=len(event.files),
                    **fields,
                )
            case "continue":
                logger.info(f"Continue {event.step}/{event.total}", **fields)
            case "skip":
                logger.info(f"Skip {event.step}/{event.total}", **fields)
            case "auto_skip":
                logger.info(f"Auto-skip {event.step}/{event.total}", **fields)
            case "abort":
                logger.info(f"Abort {event.step}/{event.total}", **fields)
            case "complete":
                logger.info("Rebase complete", **fields)
            case _:
                logger.info("Rebase event", rebase_event=event.event, **fields)

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
            raise SystemExit(1) from exc
        if not name:
            logger.error(
                'git user.name is empty in %s; set it via `git config --global user.name "Your Name"`.',
                repo_path,
            )
            raise SystemExit(1)

        try:
            email = run_git(repo_path, ["config", "--get", "user.email"]).strip()
        except GitError as exc:
            logger.exception(
                "Unable to read git user.email in %s; configure it via `git config --global user.email you@example.com`.",
                repo_path,
            )
            raise SystemExit(1) from exc
        if not email:
            logger.error(
                "git user.email is empty in %s; set it via `git config --global user.email you@example.com`.",
                repo_path,
            )
            raise SystemExit(1)

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
            raise SystemExit(1) from exc

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
            raise SystemExit(1) from exc

    def _discover_required_remotes(self, repo_path: Path) -> dict[str, GitRemote]:
        try:
            remotes = ensure_required_remotes(repo_path)
        except GitError as exc:
            logger.exception("%s", exc)
            raise SystemExit(1) from exc
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
            raise SystemExit(1) from exc

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

    def _render_terminal_summary(
        self,
        outcome: str,
        agent_log_path: Path,
        workspace: Path,
        harness_state: Path,
    ) -> None:
        """Emit the terminal-end usage summary and completion report exactly once."""

        summary = parse_usage_summary(agent_log_path, harness_state=harness_state)
        console = Console()
        render_usage_summary(outcome, summary, console=console)
        _ = render_completion_report(
            workspace, harness_state=harness_state, console=console
        )

    def _resolved_exit_code(self, code: object) -> int:
        """Normalize SystemExit payloads so re-raises preserve explicit integer codes."""

        if isinstance(code, bool):
            return 1
        if isinstance(code, int):
            return code
        return 1

    def _log_client_failure_details(self, harness_state: Path) -> None:
        """Emit the agent client log tail so harness failures are visible in host logs."""

        self._log_harness_log_tail(
            harness_state / "opencode-client.log",
            label="Agent log tail",
            tail_lines=CLIENT_LOG_TAIL_LINES,
        )

    def _log_harness_log_tail(
        self, log_path: Path, *, label: str, tail_lines: int
    ) -> None:
        """Emit a bounded tail from a harness artifact when runs fail."""

        if not log_path.exists():
            return

        try:
            log_text = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Unable to read harness log after failed run",
                path=log_path,
                error=str(exc),
            )
            return

        if not log_text.strip():
            return

        lines = log_text.splitlines()
        log_tail = "\n".join(lines[-tail_lines:])
        logger.error(
            label,
            path=log_path,
            total_lines=len(lines),
            tail_lines=min(len(lines), tail_lines),
            output=log_tail,
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
        self._log_client_failure_details(harness_state)
        raise SystemExit(1)

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

    def _emit_clientlog_hint(self, run_dir_name: str) -> None:
        """Print a boxed command for tailing the current run's client transcript."""

        command = CLIENTLOG_HINT_TEMPLATE.format(run_dir_name=shell_quote(run_dir_name))
        print(boxed(command, title=CLIENTLOG_HINT_TITLE), flush=True)

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

    def _prepare_opencode_env(self) -> OpenCodeEnv:
        try:
            env = load_opencode_env(DEFAULT_ENV_PATH)
        except OpenCodeEnvError as exc:
            logger.exception(
                "Failed to load OpenCode config from %s: %s",
                DEFAULT_ENV_PATH,
                exc,
            )
            raise SystemExit(1) from exc
        logger.info("Loaded OpenCode env", path=DEFAULT_ENV_PATH)
        logger.debug(
            "Forwarding OpenCode configuration: model=%s variant=%s agent=%s",
            env.model or "(default)",
            env.variant,
            env.agent,
        )
        return self._apply_cli_overrides(env)

    def _build_container_env(
        self,
        env: OpenCodeEnv,
        main_branch: str,
        run_id: str,
    ) -> dict[str, str]:
        return build_container_env(
            env,
            main_branch,
            run_id,
            forward_tz=self.forward_tz,
        )

    def _apply_cli_overrides(self, env: OpenCodeEnv) -> OpenCodeEnv:
        return apply_cli_overrides(
            env,
            model=self.model,
            variant=self.variant,
            agent=self.agent,
        )

    def _resolved_main_branch(self) -> str:
        return resolved_main_branch(self.main_branch)

    def _resolved_target_policy(self) -> str:
        return resolved_target_policy(self.target_policy)

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
