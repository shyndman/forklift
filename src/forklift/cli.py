from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import cast, override

from clypi import Command, arg

from .git import (
    GitError,
    GitFetchResult,
    GitRemote,
    current_branch,
    ensure_required_remotes,
    ensure_upstream_merged,
    fetch_remotes,
    run_git,
)

from .container_runner import ContainerRunner
from .opencode_env import (
    DEFAULT_ENV_PATH,
    SAFE_VALUE_PATTERN,
    OpenCodeEnv,
    OpenCodeEnvError,
    load_opencode_env,
)
from .run_manager import RunDirectoryManager, RunPaths


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

STUCK_EXIT_CODE = 4
STUCK_PREVIEW_LINES = 40


class Forklift(Command):
    """Primary entrypoint for the Forklift host orchestrator."""

    repo: Path | str | None = None
    debug: bool = arg(False, short="d", help="Enable debug logging")
    version: bool = arg(False, short="v", help="Print version and exit")
    model: str | None = arg(None, help="Override OPENCODE_MODEL (letters, numbers, punctuation ._-/).")
    variant: str | None = arg(None, help="Override OPENCODE_VARIANT (letters, numbers, punctuation ._-/).")
    agent: str | None = arg(None, help="Override OPENCODE_AGENT (letters, numbers, punctuation ._-/).")
    forward_tz: bool = arg(False, help="Forward the host TZ variable into the sandbox")
    chown: str | None = arg(
        None,
        help="Reassign harness-state ownership to UID[:GID] after runs (defaults to $UID:$GID).",
    )

    @override
    async def run(self) -> None:
        if self.version:
            self._print_version()
            return

        repo_path = self._resolve_repo_path()
        self._configure_logging()
        logging.info("Starting Forklift orchestration in %s", repo_path)

        opencode_env = self._prepare_opencode_env()
        chown_uid, chown_gid = self._resolve_chown_target()

        remotes = self._discover_required_remotes(repo_path)
        fetch_results = self._fetch_all(repo_path, remotes)

        for result in fetch_results:
            if result.output:
                logging.info("Fetch output for %s:\n%s", result.name, result.output)
            else:
                logging.info("Fetch output for %s: up to date", result.name)

        logging.info("Remote discovery and fetch complete.")

        run_manager = RunDirectoryManager()
        run_paths = run_manager.prepare(repo_path)
        logging.info(
            "Run directory ready at %s (workspace=%s, harness-state=%s, opencode-logs=%s)",
            run_paths.run_dir,
            run_paths.workspace,
            run_paths.harness_state,
            run_paths.opencode_logs,
        )

        container_env = self._build_container_env(opencode_env)
        container_runner = ContainerRunner()
        container_result = container_runner.run(
            run_paths.workspace,
            run_paths.harness_state,
            run_paths.opencode_logs,
            container_env,
        )
        self._chown_artifact(run_paths.harness_state, "harness-state", chown_uid, chown_gid)
        self._chown_artifact(run_paths.opencode_logs, "opencode-logs", chown_uid, chown_gid)
        if container_result.stdout.strip():
            logging.info("Container stdout:\n%s", container_result.stdout.strip())
        if container_result.stderr.strip():
            logging.info("Container stderr:\n%s", container_result.stderr.strip())
        if container_result.timed_out:
            logging.error(
                "Container %s timed out after %s seconds",
                container_result.container_name,
                container_runner.timeout_seconds,
            )
            raise SystemExit(2)
        if container_result.exit_code != 0:
            logging.error(
                "Container %s exited with code %s",
                container_result.container_name,
                container_result.exit_code,
            )
            raise SystemExit(container_result.exit_code)
        logging.info("Container run completed successfully.")
        self._post_container_results(repo_path, run_paths)




    def _configure_logging(self) -> None:
        level = logging.DEBUG if self.debug else logging.INFO
        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(level=level, format=LOG_FORMAT)
        else:
            root.setLevel(level)

    def _resolve_repo_path(self) -> Path:
        raw = self.repo
        if raw is None:
            base = Path.cwd()
        else:
            base = Path(raw)
        return base.expanduser().resolve()

    def _discover_required_remotes(self, repo_path: Path) -> dict[str, GitRemote]:
        try:
            remotes = ensure_required_remotes(repo_path)
        except GitError as exc:
            logging.error("%s", exc)
            raise SystemExit(1) from exc

        for remote in remotes.values():
            logging.info("Detected remote %s -> %s", remote.name, remote.fetch_url)
        return remotes

    def _fetch_all(
        self, repo_path: Path, remotes: dict[str, GitRemote]
    ) -> list[GitFetchResult]:
        try:
            return fetch_remotes(repo_path, remotes)
        except GitError as exc:
            logging.error("%s", exc)
            raise SystemExit(1) from exc

    def _post_container_results(self, repo_path: Path, run_paths: RunPaths) -> None:
        metadata = self._load_run_metadata(run_paths.run_dir)
        workspace = run_paths.workspace
        self._fail_if_stuck(workspace)

        target_branch = metadata.get("main_branch") or current_branch(workspace)
        upstream_sha = metadata.get("upstream_main_sha")

        if upstream_sha:
            try:
                ensure_upstream_merged(workspace, upstream_sha, target_branch)
                logging.info(
                    "Verified upstream commit %s is ancestor of %s",
                    upstream_sha[:12],
                    target_branch,
                )
            except GitError as exc:
                logging.error("Upstream verification failed: %s", exc)
                raise SystemExit(3) from exc
        else:
            logging.warning("Upstream commit info missing; skipping verification.")

        if self._workspace_has_changes(workspace):
            self._create_pr_stub(repo_path, target_branch)
        else:
            logging.info("No workspace changes detected; skipping PR creation.")

    def _workspace_has_changes(self, workspace: Path) -> bool:
        status = run_git(workspace, ["status", "--porcelain"])
        return bool(status.strip())

    def _fail_if_stuck(self, workspace: Path) -> None:
        stuck_file = workspace / "STUCK.md"
        if not stuck_file.exists():
            return
        logging.warning("STUCK.md detected at %s; skipping verification and PR.", stuck_file)
        try:
            contents = stuck_file.read_text().strip()
        except OSError as exc:
            logging.warning("Unable to read STUCK.md: %s", exc)
        else:
            if contents:
                preview_lines = contents.splitlines()[:STUCK_PREVIEW_LINES]
                preview_text = "\n".join(preview_lines)
                logging.warning(
                    "STUCK.md preview (first %s lines):\n%s",
                    STUCK_PREVIEW_LINES,
                    preview_text,
                )
            else:
                logging.warning("STUCK.md is empty.")
        raise SystemExit(STUCK_EXIT_CODE)


    def _load_run_metadata(self, run_dir: Path) -> dict[str, str | None]:
        metadata_path = run_dir / "metadata.json"
        try:
            raw = metadata_path.read_text()
        except FileNotFoundError:
            logging.warning("Metadata file missing at %s", metadata_path)
            return {}
        data = cast(dict[str, str | None], json.loads(raw))
        return data

    def _create_pr_stub(self, repo_path: Path, branch: str) -> None:
        logging.info(
            "PR stub: use host repo at %s to push branch %s and run `gh pr create --head %s --base main`.",
            repo_path,
            branch,
            branch,
        )

    def _prepare_opencode_env(self) -> OpenCodeEnv:
        env_path = DEFAULT_ENV_PATH
        try:
            env = load_opencode_env(env_path)
        except OpenCodeEnvError as exc:
            logging.error("Failed to load OpenCode config from %s: %s", env_path, exc)
            raise SystemExit(1) from exc
        logging.info("Loaded OpenCode env from %s", env_path)
        logging.debug(
            "Forwarding OpenCode configuration: model=%s variant=%s agent=%s",
            env.model or "(default)",
            env.variant,
            env.agent,
        )
        return env

    def _build_container_env(self, env: OpenCodeEnv) -> dict[str, str]:
        container_env = dict(env.as_env())
        tz_value = self._host_timezone_value()
        if tz_value is not None:
            container_env["TZ"] = tz_value
        return container_env

    def _host_timezone_value(self) -> str | None:
        if not self.forward_tz:
            return None
        tz_value = os.environ.get("TZ")
        if not tz_value:
            logging.warning("--forward-tz enabled but host TZ is unset; skipping TZ forwarding.")
            return None
        if self._contains_control_characters(tz_value):
            logging.warning("Host TZ value %r contains control characters; skipping TZ forwarding.", tz_value)
            return None
        logging.info("Forwarding host TZ=%s into sandbox container.", tz_value)
        return tz_value

    @staticmethod
    def _contains_control_characters(value: str) -> bool:
        return any(ord(char) < 32 or ord(char) == 127 for char in value)

    def _apply_cli_overrides(self, env: OpenCodeEnv) -> OpenCodeEnv:
        model = self._validated_override(self.model, env.model, "model")
        variant = self._validated_override(self.variant, env.variant, "variant")
        agent = self._validated_override(self.agent, env.agent, "agent")
        return replace(env, model=model, variant=variant, agent=agent)

    def _validated_override(
        self, override: str | None, current: str | None, label: str
    ) -> str | None:
        if override is None:
            return current
        if not SAFE_VALUE_PATTERN.fullmatch(override):
            logging.error(
                "Invalid %s value %r; expected pattern %s",
                label,
                override,
                SAFE_VALUE_PATTERN.pattern,
            )
            raise SystemExit(1)
        return override

    def _print_version(self) -> None:
        try:
            pkg_version = metadata.version("forklift")
        except metadata.PackageNotFoundError:
            pkg_version = "unknown"
        print(pkg_version)

    def _resolve_chown_target(self) -> tuple[int, int]:
        default_uid, default_gid = self._default_host_ids()
        spec = (self.chown or "").strip()
        if not spec:
            return default_uid, default_gid
        uid_part, _, gid_part = spec.partition(":")
        uid_part = uid_part.strip()
        gid_part = gid_part.strip()
        if not uid_part:
            logging.error("Invalid --chown value %r; UID is required.", spec)
            raise SystemExit(1)
        uid = self._parse_id_component(uid_part, "UID")
        gid = (
            default_gid
            if gid_part == ""
            else self._parse_id_component(gid_part, "GID")
        )
        return uid, gid

    def _default_host_ids(self) -> tuple[int, int]:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        return uid, gid

    def _parse_id_component(self, raw: str, label: str) -> int:
        try:
            value = int(raw, 10)
        except ValueError:
            logging.error("Invalid %s %r in --chown value; expected integer.", label, raw)
            raise SystemExit(1) from None
        if value < 0:
            logging.error(
                "Invalid %s %s in --chown value; expected non-negative integer.", label, value
            )
            raise SystemExit(1)
        return value

    def _chown_artifact(self, target: Path, label: str, uid: int, gid: int) -> None:
        if not target.exists():
            logging.debug("%s directory %s missing; skipping ownership reset.", label, target)
            return
        logging.info("Reassigning %s ownership to %s:%s", label, uid, gid)
        try:
            self._chown_path_recursive(target, uid, gid)
        except PermissionError as exc:
            logging.warning(
                "Unable to chown %s to %s:%s: %s", label, uid, gid, exc
            )
        except OSError as exc:
            logging.warning(
                "Failed to chown %s to %s:%s: %s", label, uid, gid, exc
            )

    def _chown_path_recursive(self, path: Path, uid: int, gid: int) -> None:
        self._set_owner(path, uid, gid)
        if path.is_symlink():
            return
        try:
            is_dir = path.is_dir()
        except OSError:
            return
        if not is_dir:
            return
        try:
            children = list(path.iterdir())
        except OSError:
            return
        for child in children:
            try:
                self._chown_path_recursive(child, uid, gid)
            except FileNotFoundError:
                continue

    def _set_owner(self, path: Path, uid: int, gid: int) -> None:
        try:
            os.chown(path, uid, gid, follow_symlinks=False)
        except NotImplementedError:
            os.chown(path, uid, gid)
