# This comment is part of an experiment requested by the repo owner.
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
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

AGENT_NAME = "Forklift Agent"
AGENT_EMAIL = "forklift@github.com"
STASH_MESSAGE = "forklift-authorship-rewrite"
FILTER_REPO_INSTALL_HELP = (
    "Install git filter-repo 2.47.0+: pip install git-filter-repo==2.47.0, "
    "brew install git-filter-repo, or download the standalone script from "
    "https://github.com/newren/git-filter-repo/releases (requires git >= 2.22 and python >= 3.6)."
)


@dataclass(frozen=True)
class OperatorIdentity:
    name: str
    email: str


@dataclass
class RewriteResult:
    branch: str
    operator: OperatorIdentity
    origin_sha: str
    tag_name: str | None
    stash_created: bool
    stash_conflicts: bool
    pushed: bool


class Forklift(Command):
    """Primary entrypoint for the Forklift host orchestrator."""

    repo: Path | str | None = None
    main_branch: str = arg("main", help="Name of the primary branch to rebase (default: main)")
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

        operator_identity = self._capture_operator_identity(repo_path)
        main_branch = self._resolved_main_branch()

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
        metadata_overrides = self._metadata_overrides(operator_identity, remotes)
        run_paths = run_manager.prepare(
            repo_path,
            main_branch=main_branch,
            extra_metadata=metadata_overrides,
        )
        logging.info(
            "Run directory ready at %s (workspace=%s, harness-state=%s, opencode-logs=%s)",
            run_paths.run_dir,
            run_paths.workspace,
            run_paths.harness_state,
            run_paths.opencode_logs,
        )

        container_env = self._build_container_env(opencode_env, main_branch)
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
        self._post_container_results(repo_path, run_paths, main_branch)




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

    def _capture_operator_identity(self, repo_path: Path) -> OperatorIdentity:
        try:
            name = run_git(repo_path, ["config", "--get", "user.name"]).strip()
        except GitError as exc:
            logging.error(
                "Unable to read git user.name in %s; configure it via `git config --global user.name \"Your Name\"`.",
                repo_path,
            )
            raise SystemExit(1) from exc
        if not name:
            logging.error(
                "git user.name is empty in %s; set it via `git config --global user.name \"Your Name\"`.",
                repo_path,
            )
            raise SystemExit(1)
        try:
            email = run_git(repo_path, ["config", "--get", "user.email"]).strip()
        except GitError as exc:
            logging.error(
                "Unable to read git user.email in %s; configure it via `git config --global user.email you@example.com`.",
                repo_path,
            )
            raise SystemExit(1) from exc
        if not email:
            logging.error(
                "git user.email is empty in %s; set it via `git config --global user.email you@example.com`.",
                repo_path,
            )
            raise SystemExit(1)
        logging.info("Captured operator identity %s <%s>", name, email)
        return OperatorIdentity(name=name, email=email)

    def _metadata_overrides(
        self, identity: OperatorIdentity, remotes: dict[str, GitRemote]
    ) -> dict[str, object]:
        remote_entries = {
            name: {"fetch_url": remote.fetch_url}
            for name, remote in remotes.items()
        }
        return {
            "operator_name": identity.name,
            "operator_email": identity.email,
            "remotes": remote_entries,
        }

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

    def _post_container_results(
        self, repo_path: Path, run_paths: RunPaths, configured_branch: str
    ) -> None:
        metadata = self._load_run_metadata(run_paths.run_dir)
        workspace = run_paths.workspace
        self._fail_if_stuck(workspace)

        metadata_branch = cast(str | None, metadata.get("main_branch"))
        target_branch = metadata_branch or configured_branch or current_branch(workspace)
        upstream_ref_branch = metadata_branch or configured_branch
        upstream_ref = f"upstream/{upstream_ref_branch}"
        upstream_sha = cast(str | None, metadata.get("upstream_main_sha"))

        try:
            ensure_upstream_merged(workspace, upstream_ref, target_branch)
            if upstream_sha:
                logging.info(
                    "Verified %s (%s) is ancestor of %s",
                    upstream_ref,
                    upstream_sha[:12],
                    target_branch,
                )
            else:
                logging.info(
                    "Verified %s is ancestor of %s",
                    upstream_ref,
                    target_branch,
                )
        except GitError as exc:
            logging.error("Upstream verification failed: %s", exc)
            raise SystemExit(3) from exc

        rewrite_result = self._rewrite_and_push(run_paths, metadata, target_branch)
        self._log_rewrite_summary(rewrite_result)
        self._create_pr_stub(repo_path, target_branch, rewrite_result)

    def _workspace_has_changes(self, workspace: Path) -> bool:
        status = run_git(workspace, ["status", "--porcelain"])
        return bool(status.strip())

    def _rewrite_and_push(
        self, run_paths: RunPaths, metadata: dict[str, object], target_branch: str
    ) -> RewriteResult | None:
        operator_name = cast(str | None, metadata.get("operator_name"))
        operator_email = cast(str | None, metadata.get("operator_email"))
        origin_sha = cast(str | None, metadata.get("origin_main_sha"))
        remotes_data = metadata.get("remotes")
        if not operator_name or not operator_email or not origin_sha:
            logging.info(
                "Missing operator identity or origin baseline in metadata; skipping rewrite/push."
            )
            return None
        if not isinstance(remotes_data, dict):
            logging.warning("Remote metadata unavailable; skipping rewrite/push.")
            return None
        operator = OperatorIdentity(name=operator_name, email=operator_email)
        remote_urls: dict[str, str] = {}
        remotes_dict = cast(dict[str, object], remotes_data)
        for remote_name in ("origin", "upstream"):
            entry = remotes_dict.get(remote_name)
            if not isinstance(entry, dict):
                logging.warning("Remote metadata for %s missing; skipping rewrite/push.", remote_name)
                return None
            entry_dict = cast(dict[str, object], entry)
            fetch_url = entry_dict.get("fetch_url")
            if not isinstance(fetch_url, str) or not fetch_url:
                logging.warning(
                    "Remote metadata for %s lacks fetch_url; skipping rewrite/push.", remote_name
                )
                return None
            remote_urls[remote_name] = fetch_url

        workspace = run_paths.workspace
        stash_created = False
        stash_conflicts = False
        try:
            if self._workspace_has_changes(workspace):
                logging.info("Stashing workspace state before rewrite (%s)", STASH_MESSAGE)
                _ = run_git(workspace, ["stash", "push", "-u", "-m", STASH_MESSAGE])
                stash_created = True

            current = current_branch(workspace)
            if current != target_branch:
                logging.info("Checking out %s before rewrite (current branch %s)", target_branch, current)
                _ = run_git(workspace, ["checkout", target_branch])

            current_head = run_git(workspace, ["rev-parse", "HEAD"]).strip()
            if current_head == origin_sha:
                logging.info(
                    "Branch %s head %s matches stored origin %s; skipping rewrite/push.",
                    target_branch,
                    current_head[:12],
                    origin_sha[:12],
                )
                if stash_created:
                    stash_conflicts = not self._pop_stash(workspace)
                return RewriteResult(
                    branch=target_branch,
                    operator=operator,
                    origin_sha=origin_sha,
                    tag_name=None,
                    stash_created=stash_created,
                    stash_conflicts=stash_conflicts,
                    pushed=False,
                )

            for remote_name, url in remote_urls.items():
                self._ensure_remote(workspace, remote_name, url)
            for remote_name in remote_urls:
                fetch_output = run_git(workspace, ["fetch", remote_name, "--prune"])
                if fetch_output:
                    logging.info("Workspace fetch output for %s:\n%s", remote_name, fetch_output)

            self._validate_filter_repo(workspace)
            mailmap_path = self._write_mailmap(run_paths.run_dir, operator)
            try:
                _ = run_git(
                    workspace,
                    [
                        "filter-repo",
                        "--force",
                        f"--mailmap={mailmap_path}",
                        f"--refs=refs/heads/{target_branch}",
                    ],
                )
            finally:
                try:
                    mailmap_path.unlink()
                except FileNotFoundError:
                    pass

            self._assert_no_agent_commits(workspace)

            tag_timestamp = cast(str | None, metadata.get("created_at")) or "latest"
            tag_name = f"forklift/{target_branch}/{tag_timestamp}/pre-push"
            _ = run_git(workspace, ["tag", "-f", tag_name, origin_sha])

            push_output = run_git(
                workspace,
                [
                    "push",
                    "origin",
                    f"{target_branch}:{target_branch}",
                    f"--force-with-lease={target_branch}:{origin_sha}",
                ],
            )
            if push_output:
                logging.info("Push output:\n%s", push_output)

            if stash_created:
                stash_conflicts = not self._pop_stash(workspace)

            return RewriteResult(
                branch=target_branch,
                operator=operator,
                origin_sha=origin_sha,
                tag_name=tag_name,
                stash_created=stash_created,
                stash_conflicts=stash_conflicts,
                pushed=True,
            )
        except GitError as exc:
            logging.error("Failed to rewrite/push rewritten branch: %s", exc)
            if stash_created:
                logging.warning(
                    "Stash '%s' remains on the stack; recover it later via `git stash list` inside %s.",
                    STASH_MESSAGE,
                    workspace,
                )
            raise SystemExit(1) from exc

    def _ensure_remote(self, workspace: Path, name: str, url: str) -> None:
        try:
            current_url = run_git(workspace, ["remote", "get-url", name])
        except GitError:
            logging.info("Reattaching remote %s -> %s", name, url)
            _ = run_git(workspace, ["remote", "add", name, url])
            return
        if current_url == url:
            return
        logging.info("Updating remote %s to %s (was %s)", name, url, current_url)
        _ = run_git(workspace, ["remote", "set-url", name, url])

    def _validate_filter_repo(self, workspace: Path) -> None:
        try:
            version = run_git(workspace, ["filter-repo", "--version"]).strip()
        except GitError as exc:
            logging.error("git filter-repo not available: %s", exc)
            logging.error(FILTER_REPO_INSTALL_HELP)
            raise SystemExit(1) from exc
        if version:
            logging.info("git filter-repo detected (%s)", version)

    def _write_mailmap(self, run_dir: Path, operator: OperatorIdentity) -> Path:
        mailmap_path = run_dir / "authorship.mailmap"
        mapping = f"{operator.name} <{operator.email}> {AGENT_NAME} <{AGENT_EMAIL}>\n"
        _ = mailmap_path.write_text(mapping)
        return mailmap_path

    def _assert_no_agent_commits(self, workspace: Path) -> None:
        residual = run_git(
            workspace,
            [
                "log",
                "--all",
                "--format=%H",
                f"--author={AGENT_NAME} <{AGENT_EMAIL}>",
            ],
        ).strip()
        if residual:
            sample = ", ".join(residual.splitlines()[:5])
            logging.error(
                "Authorship rewrite incomplete; commits authored by %s <%s> remain: %s",
                AGENT_NAME,
                AGENT_EMAIL,
                sample,
            )
            raise SystemExit(1)

    def _pop_stash(self, workspace: Path) -> bool:
        try:
            output = run_git(workspace, ["stash", "pop"])
        except GitError as exc:
            logging.warning(
                "Unable to auto-pop stash '%s': %s. Recover manually via `git stash list`.",
                STASH_MESSAGE,
                exc,
            )
            return False
        if output:
            logging.info("Stash pop output:\n%s", output)
        return True

    def _log_rewrite_summary(self, result: RewriteResult | None) -> None:
        if result is None:
            logging.info("Rewrite/push pipeline skipped (metadata incomplete).")
            return
        if not result.pushed:
            logging.info(
                "Branch %s already matched origin %s; no rewrite/push required.",
                result.branch,
                result.origin_sha[:12],
            )
            if result.stash_created:
                if result.stash_conflicts:
                    logging.warning(
                        "Stash '%s' reapplied with conflicts; inspect the workspace and use `git stash list` if needed.",
                        STASH_MESSAGE,
                    )
                else:
                    logging.info("Stash '%s' reapplied cleanly.", STASH_MESSAGE)
            return

        logging.info(
            "Authorship rewrite complete: branch %s force-pushed to origin/%s with commits rewritten to %s <%s>.",
            result.branch,
            result.branch,
            result.operator.name,
            result.operator.email,
        )
        if result.tag_name:
            logging.info(
                "Local safety tag %s points to baseline %s.",
                result.tag_name,
                result.origin_sha[:12],
            )
        if result.stash_created:
            if result.stash_conflicts:
                logging.warning(
                    "Stash '%s' reapplied with conflicts; recover manually via `git stash list` and resolve merges.",
                    STASH_MESSAGE,
                )
            else:
                logging.info("Stash '%s' reapplied cleanly.", STASH_MESSAGE)

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


    def _load_run_metadata(self, run_dir: Path) -> dict[str, object]:
        metadata_path = run_dir / "metadata.json"
        try:
            raw = metadata_path.read_text()
        except FileNotFoundError:
            logging.warning("Metadata file missing at %s", metadata_path)
            return {}
        data = cast(dict[str, object], json.loads(raw))
        return data

    def _create_pr_stub(
        self, repo_path: Path, branch: str, result: RewriteResult | None
    ) -> None:
        if result is None or not result.pushed:
            logging.info(
                "PR stub: no rewritten commits were pushed for %s; nothing to do in %s.",
                branch,
                repo_path,
            )
            return
        logging.info(
            "PR stub: branch %s is already on origin/%s. Run `gh pr create --head %s --base %s` from %s when ready.",
            branch,
            branch,
            branch,
            branch,
            repo_path,
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

    def _build_container_env(self, env: OpenCodeEnv, main_branch: str) -> dict[str, str]:
        container_env = dict(env.as_env())
        container_env["FORKLIFT_MAIN_BRANCH"] = main_branch
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

    def _resolved_main_branch(self) -> str:
        branch = (self.main_branch or "main").strip()
        if not branch:
            logging.error("--main-branch value must not be empty")
            raise SystemExit(1)
        if not SAFE_VALUE_PATTERN.fullmatch(branch):
            logging.error(
                "Invalid --main-branch value %r; expected pattern %s",
                branch,
                SAFE_VALUE_PATTERN.pattern,
            )
            raise SystemExit(1)
        return branch

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
