from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import cast, override

HARNESS_SCRIPT = Path(__file__).resolve().parents[1] / "docker/kitchen-sink/harness/run.sh"


class RebaseEventServer:
    socket_path: Path
    events: list[dict[str, object]]
    _listener: socket.socket | None
    _stop: threading.Event
    _thread: threading.Thread

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.events = []
        self._listener = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> RebaseEventServer:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.settimeout(0.2)
        self._listener.bind(str(self.socket_path))
        self._listener.listen()
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        self._thread.join(timeout=1)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def wait_for_event_count(self, expected: int) -> None:
        deadline = time.time() + 1
        while len(self.events) < expected and time.time() < deadline:
            time.sleep(0.01)

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                accepted = cast(tuple[socket.socket, object], self._listener.accept())
                connection = accepted[0]
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                raise

            with connection:
                chunks: list[bytes] = []
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                for line in b"".join(chunks).decode("utf-8").splitlines():
                    if line.strip():
                        payload_obj = cast(object, json.loads(line))
                        if isinstance(payload_obj, dict):
                            self.events.append(cast(dict[str, object], payload_obj))


class HarnessSetupTests(unittest.TestCase):
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path = Path(".")
    workspace: Path = Path(".")
    harness_state: Path = Path(".")

    @override
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.workspace = self.root / "workspace"
        self.harness_state = self.root / "harness-state"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.harness_state.mkdir(parents=True, exist_ok=True)

    @override
    def tearDown(self) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()

    def _run_harness_shell(self, commands: str) -> subprocess.CompletedProcess[str]:
        script = f"""
set -euo pipefail
source \"{HARNESS_SCRIPT}\"
WORKSPACE_DIR="{self.workspace}"
HARNESS_STATE_DIR="{self.harness_state}"
INSTRUCTIONS_FILE="$HARNESS_STATE_DIR/instructions.txt"
FORK_CONTEXT_FILE="$HARNESS_STATE_DIR/fork-context.md"
EXTRA_RUN_INSTRUCTIONS_FILE="$HARNESS_STATE_DIR/extra-run-instructions.md"
SETUP_LOG="$HARNESS_STATE_DIR/setup.log"
CLIENT_LOG="$HARNESS_STATE_DIR/opencode-client.log"
HARNESS_STATUS_FILE="$HARNESS_STATE_DIR/harness-status.txt"
REBASE_CONTINUE_CHECK_FILE="$HARNESS_STATE_DIR/rebase-continue-check.sh"
REBASE_SKIPPED_COMMITS_FILE="$HARNESS_STATE_DIR/rebase-skipped-commits.json"
REBASE_CONFLICTING_COMMITS_FILE="$HARNESS_STATE_DIR/rebase-conflicting-commits.json"
export WORKSPACE_DIR HARNESS_STATE_DIR INSTRUCTIONS_FILE FORK_CONTEXT_FILE EXTRA_RUN_INSTRUCTIONS_FILE SETUP_LOG CLIENT_LOG HARNESS_STATUS_FILE
export REBASE_CONTINUE_CHECK_FILE REBASE_SKIPPED_COMMITS_FILE REBASE_CONFLICTING_COMMITS_FILE
: >"$CLIENT_LOG"
: >"$SETUP_LOG"
{commands}
"""
        return subprocess.run(
            ["bash", "-lc", script],
            text=True,
            capture_output=True,
            check=False,
        )

    def _run_git_with_commit_identity(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "Fixture Author"
        env["GIT_AUTHOR_EMAIL"] = "fixture-author@example.com"
        env["GIT_COMMITTER_NAME"] = "Fixture Committer"
        env["GIT_COMMITTER_EMAIL"] = "fixture-committer@example.com"
        return subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def _init_workspace_repo(self) -> None:
        _ = subprocess.run(["git", "init"], cwd=self.workspace, check=True)
        _ = subprocess.run(
            ["git", "config", "user.name", "Harness Test"],
            cwd=self.workspace,
            check=True,
        )
        _ = subprocess.run(
            ["git", "config", "user.email", "harness-test@example.com"],
            cwd=self.workspace,
            check=True,
        )
        tracked = self.workspace / "tracked.txt"
        _ = tracked.write_text("baseline\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "tracked.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=self.workspace,
            check=True,
        )

    def _init_conflicting_rebase(self) -> None:
        _ = subprocess.run(["git", "init", "-b", "main"], cwd=self.workspace, check=True)
        _ = subprocess.run(
            ["git", "config", "user.name", "Harness Test"],
            cwd=self.workspace,
            check=True,
        )
        _ = subprocess.run(
            ["git", "config", "user.email", "harness-test@example.com"],
            cwd=self.workspace,
            check=True,
        )

        tracked = self.workspace / "tracked.txt"
        _ = tracked.write_text("base\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "tracked.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "commit", "-m", "base"], cwd=self.workspace, check=True)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        _ = tracked.write_text("fork\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "tracked.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "commit", "-m", "fork change"], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "branch", "upstream/main", base_sha], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "checkout", "upstream/main"], cwd=self.workspace, check=True)
        _ = tracked.write_text("upstream\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "tracked.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(
            ["git", "commit", "-m", "upstream change"],
            cwd=self.workspace,
            check=True,
        )
        _ = subprocess.run(["git", "checkout", "main"], cwd=self.workspace, check=True)
        result = subprocess.run(
            ["git", "rebase", "upstream/main"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.workspace / ".git" / "rebase-merge").exists())

    def _init_clean_rebase(self) -> None:
        _ = subprocess.run(["git", "init", "-b", "main"], cwd=self.workspace, check=True)
        _ = subprocess.run(
            ["git", "config", "user.name", "Harness Test"],
            cwd=self.workspace,
            check=True,
        )
        _ = subprocess.run(
            ["git", "config", "user.email", "harness-test@example.com"],
            cwd=self.workspace,
            check=True,
        )

        tracked = self.workspace / "tracked.txt"
        _ = tracked.write_text("base\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "tracked.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "commit", "-m", "base"], cwd=self.workspace, check=True)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        fork_only = self.workspace / "fork-only.txt"
        _ = fork_only.write_text("fork\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "fork-only.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "commit", "-m", "fork change"], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "branch", "upstream/main", base_sha], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "checkout", "upstream/main"], cwd=self.workspace, check=True)
        upstream_only = self.workspace / "upstream-only.txt"
        _ = upstream_only.write_text("upstream\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "upstream-only.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(
            ["git", "commit", "-m", "upstream change"],
            cwd=self.workspace,
            check=True,
        )
        _ = subprocess.run(["git", "checkout", "main"], cwd=self.workspace, check=True)

    def _init_rebase_repo_without_configured_identity(self) -> None:
        _ = subprocess.run(["git", "init", "-b", "main"], cwd=self.workspace, check=True)

        tracked = self.workspace / "tracked.txt"
        _ = tracked.write_text("base\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "tracked.txt"], cwd=self.workspace, check=True)
        _ = self._run_git_with_commit_identity(["commit", "-m", "base"])
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        fork_only = self.workspace / "fork-only.txt"
        _ = fork_only.write_text("fork\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "fork-only.txt"], cwd=self.workspace, check=True)
        _ = self._run_git_with_commit_identity(["commit", "-m", "fork change"])
        _ = subprocess.run(["git", "branch", "upstream/main", base_sha], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "checkout", "upstream/main"], cwd=self.workspace, check=True)
        upstream_only = self.workspace / "upstream-only.txt"
        _ = upstream_only.write_text("upstream\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "upstream-only.txt"], cwd=self.workspace, check=True)
        _ = self._run_git_with_commit_identity(["commit", "-m", "upstream change"])
        _ = subprocess.run(["git", "checkout", "main"], cwd=self.workspace, check=True)

    def _init_main_repo_without_upstream(self) -> None:
        _ = subprocess.run(["git", "init", "-b", "main"], cwd=self.workspace, check=True)
        _ = subprocess.run(
            ["git", "config", "user.name", "Harness Test"],
            cwd=self.workspace,
            check=True,
        )
        _ = subprocess.run(
            ["git", "config", "user.email", "harness-test@example.com"],
            cwd=self.workspace,
            check=True,
        )

        tracked = self.workspace / "tracked.txt"
        _ = tracked.write_text("base\n", encoding="utf-8")
        _ = subprocess.run(["git", "add", "tracked.txt"], cwd=self.workspace, check=True)
        _ = subprocess.run(["git", "commit", "-m", "base"], cwd=self.workspace, check=True)

    def _event_socket_path(self) -> Path:
        return self.root / "control" / "rebase-events.sock"

    def _init_clean_empty_rebase_stop(self, *, dirty: bool = False) -> tuple[Path, Path]:
        self._init_workspace_repo()
        rebase_merge = self.workspace / ".git" / "rebase-merge"
        rebase_merge.mkdir(parents=True, exist_ok=True)
        _ = (rebase_merge / "msgnum").write_text("1\n", encoding="utf-8")
        _ = (rebase_merge / "end").write_text("1\n", encoding="utf-8")

        fake_git = self.harness_state / "fake-git.sh"
        command_log = self.harness_state / "fake-git-commands.txt"
        status_output = "?? generated.txt\\n" if dirty else ""
        _ = fake_git.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    f'printf "%s\\n" "$*" >>"{command_log}"',
                    'if [[ "$1" == "-C" ]]; then',
                    '  workspace_dir="$2"',
                    "  shift 2",
                    "else",
                    '  workspace_dir=""',
                    "fi",
                    'if [[ "$1" == "status" ]]; then',
                    f'  printf "{status_output}"',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rev-parse" && "$2" == "REBASE_HEAD" ]]; then',
                    "  exit 1",
                    "fi",
                    'if [[ "$1" == "show" && "$2" == "-s" && "$3" == "--format=%s" && "$4" == "REBASE_HEAD" ]]; then',
                    "  exit 1",
                    "fi",
                    'if [[ "$1" == "diff" && "$2" == "--name-only" && "$3" == "--diff-filter=U" ]]; then',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rev-list" && "$2" == "--count" ]]; then',
                    "  printf '1\\n'",
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rebase" && "$2" == "upstream/main" ]]; then',
                    "  exit 1",
                    "fi",
                    'if [[ "$1" == "rebase" && "$2" == "--skip" ]]; then',
                    '  rm -rf "$workspace_dir/.git/rebase-merge"',
                    "  exit 0",
                    "fi",
                    'printf "unexpected fake git args: %s\\n" "$*" >&2',
                    "exit 99",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        return fake_git, command_log

    def test_parse_fork_context_without_front_matter_treats_whole_file_as_body(self) -> None:
        fork_file = self.workspace / "FORK.md"
        _ = fork_file.write_text("## Mission\nKeep behavior stable.\n", encoding="utf-8")

        result = self._run_harness_shell(
            """
parse_fork_context
printf '%s' "$FORK_SETUP_COMMAND" >"$HARNESS_STATE_DIR/setup-command.txt"
printf '%s' "$FORK_CONTEXT_BODY" >"$HARNESS_STATE_DIR/fork-body.txt"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual((self.harness_state / "setup-command.txt").read_text(encoding="utf-8"), "")
        expected_body = fork_file.read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(
            (self.harness_state / "fork-body.txt").read_text(encoding="utf-8"),
            expected_body,
        )

    def test_setup_changelog_and_rebase_metadata_are_supported(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "\n".join(
                [
                    "---",
                    "setup: |",
                    "  echo bootstrap-ok",
                    "changelog:",
                    "  exclude:",
                    "    - data/big-snapshot.json",
                    "    - !data/keep.json",
                    "rebase:",
                    "  continue_check: |",
                    "    echo continue-ok",
                    "---",
                    "## Mission",
                    "Preserve custom behavior.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
run_setup_command
printf '%s' "$FORK_CHANGELOG_EXCLUDE_PATTERNS" >"$HARNESS_STATE_DIR/changelog-excludes.txt"
printf '%s' "$FORK_REBASE_CONTINUE_CHECK" >"$HARNESS_STATE_DIR/rebase-continue-check.txt"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        excludes = (self.harness_state / "changelog-excludes.txt").read_text(
            encoding="utf-8"
        )
        continue_check = (self.harness_state / "rebase-continue-check.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(excludes, "data/big-snapshot.json\n!data/keep.json")
        self.assertEqual(continue_check, "echo continue-ok")
        setup_log = (self.harness_state / "setup.log").read_text(encoding="utf-8")
        self.assertIn("bootstrap-ok", setup_log)

    def test_parse_fork_context_fails_closed_on_invalid_changelog_shape(self) -> None:
        _ = (self.workspace / "FORK.md").write_text(
            "---\nchangelog: []\n---\n## Mission\nNope\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
if parse_fork_context; then
  echo "expected parse failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("changelog must be an object", result.stderr)

    def test_parse_fork_context_fails_closed_on_invalid_changelog_exclude_entry(self) -> None:
        _ = (self.workspace / "FORK.md").write_text(
            "\n".join(
                [
                    "---",
                    "changelog:",
                    "  exclude:",
                    "    -",
                    "      nested: value",
                    "---",
                    "## Mission",
                    "Nope",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
if parse_fork_context; then
  echo "expected parse failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("changelog.exclude entries must be non-empty strings", result.stderr)

    def test_parse_fork_context_supports_inline_rebase_continue_check(self) -> None:
        _ = (self.workspace / "FORK.md").write_text(
            "---\nrebase:\n  continue_check: echo inline-ok\n---\n## Mission\nKeep going\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
printf '%s' "$FORK_REBASE_CONTINUE_CHECK" >"$HARNESS_STATE_DIR/rebase-inline.txt"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            (self.harness_state / "rebase-inline.txt").read_text(encoding="utf-8"),
            "echo inline-ok",
        )

    def test_parse_fork_context_fails_closed_on_invalid_rebase_shape(self) -> None:
        _ = (self.workspace / "FORK.md").write_text(
            "---\nrebase: []\n---\n## Mission\nNope\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
if parse_fork_context; then
  echo "expected parse failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("rebase must be an object", result.stderr)

    def test_parse_fork_context_fails_closed_on_unknown_rebase_key(self) -> None:
        _ = (self.workspace / "FORK.md").write_text(
            "---\nrebase:\n  nope: echo bad\n---\n## Mission\nNope\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
if parse_fork_context; then
  echo "expected parse failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Only 'continue_check' is allowed", result.stderr)

    def test_rebase_continue_check_snapshot_stays_frozen_after_fork_md_changes(self) -> None:
        frozen_output = self.harness_state / "frozen-output.txt"
        _ = (self.workspace / "FORK.md").write_text(
            f"---\nrebase:\n  continue_check: printf frozen > {frozen_output}\n---\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
write_rebase_continue_check_file
cat >"$WORKSPACE_DIR/FORK.md" <<'EOF'
---
rebase:
  continue_check: printf changed > ignored.txt
---
EOF
bash "$REBASE_CONTINUE_CHECK_FILE"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(frozen_output.read_text(encoding="utf-8"), "frozen")

    def test_setup_success_writes_setup_log(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "".join(
                [
                    "---\n",
                    "setup: |\n",
                    "  printf '%s\\n' setup-stdout\n",
                    "  printf '%s\\n' setup-stderr >&2\n",
                    "---\n",
                    "## Mission\n",
                    "Preserve custom behavior.\n",
                ]
            ),
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
run_setup_command
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        setup_log = (self.harness_state / "setup.log").read_text(encoding="utf-8")
        self.assertIn("setup-stdout", result.stdout)
        self.assertIn("setup-stderr", result.stderr)
        self.assertIn("setup-stdout", setup_log)
        self.assertIn("setup-stderr", setup_log)
        client_log = (self.harness_state / "opencode-client.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("[setup] setup-stdout", client_log)
        self.assertIn("[setup] setup-stderr", client_log)

    def test_configure_git_lfs_filters_installs_global_filters_when_available(self) -> None:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        commands_log = self.harness_state / "git-lfs-commands.txt"
        git_script = bin_dir / "git"
        _ = git_script.write_text(
            "".join(
                [
                    "#!/usr/bin/env bash\n",
                    "set -euo pipefail\n",
                    f"printf '%s\\n' \"$*\" >>{commands_log}\n",
                    "if [[ \"${1:-}\" == \"lfs\" && \"${2:-}\" == \"version\" ]]; then\n",
                    "  printf 'git-lfs/9.9.9\\n'\n",
                    "fi\n",
                ]
            ),
            encoding="utf-8",
        )
        git_script.chmod(0o755)
        git_lfs_script = bin_dir / "git-lfs"
        _ = git_lfs_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        git_lfs_script.chmod(0o755)

        result = self._run_harness_shell(
            f'''
PATH="{bin_dir}:$PATH"
configure_git_lfs_filters
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            commands_log.read_text(encoding="utf-8").splitlines(),
            ["lfs install --skip-repo", "lfs version"],
        )
        client_log = (self.harness_state / "opencode-client.log").read_text(encoding="utf-8")
        self.assertIn("Configuring Git LFS filters", client_log)
        self.assertIn("git-lfs=git-lfs/9.9.9", client_log)

    def test_parse_fork_context_fails_closed_on_malformed_front_matter(self) -> None:
        _ = (self.workspace / "FORK.md").write_text(
            "---\nsetup: echo hi\n## Mission\nNo closing delimiter\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
if parse_fork_context; then
  echo "expected parse failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("missing closing '---' delimiter", result.stderr)

    def test_setup_non_zero_fails_closed(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "---\nsetup: false\n---\n## Mission\nPreserve custom behavior.\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
if run_setup_command; then
  echo "expected setup failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        setup_log = (self.harness_state / "setup.log").read_text(encoding="utf-8")
        self.assertIn("Setup command failed with exit code", setup_log)
        self.assertIn("Setup command failed with exit code 1", result.stderr)
        self.assertIn("Setup Diagnostics", result.stdout)

    def test_fail_harness_records_status_details(self) -> None:
        result = self._run_harness_shell(
            """
HARNESS_PHASE=setup
fail_harness "setup exploded"
"""
        )

        self.assertEqual(result.returncode, 1)
        status = (self.harness_state / "harness-status.txt").read_text(encoding="utf-8")
        self.assertIn("status=failed", status)
        self.assertIn("phase=setup", status)
        self.assertIn("message=setup exploded", status)

    def test_setup_timeout_defaults_to_ten_minutes(self) -> None:
        result = self._run_harness_shell(
            """
printf 'default_setup_timeout=%s\n' "$SETUP_TIMEOUT_SECONDS"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("default_setup_timeout=600", result.stdout)

    def test_setup_timeout_fails_closed(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "---\nsetup: sleep 2\n---\n## Mission\nPreserve custom behavior.\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
SETUP_TIMEOUT_SECONDS=1
parse_fork_context
if run_setup_command; then
  echo "expected setup timeout failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        setup_log = (self.harness_state / "setup.log").read_text(encoding="utf-8")
        self.assertIn("timed out", setup_log)

    def test_setup_dirty_worktree_fails_closed(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "---\nsetup: echo changed > tracked.txt\n---\n## Mission\nPreserve custom behavior.\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
if run_setup_command; then
  echo "expected dirty-worktree failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        setup_log = (self.harness_state / "setup.log").read_text(encoding="utf-8")
        self.assertIn("Tracked Changes After Setup", setup_log)
        self.assertIn("tracked.txt", setup_log)
        self.assertIn("Tracked Changes After Setup", result.stderr)

    def test_front_matter_is_stripped_from_agent_visible_artifacts(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "---\nsetup: echo bootstrap-ok\n---\n## Mission\nKeep TV UX behavior.\n",
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
run_setup_command
write_instructions
build_agent_payload
printf '%s' "$AGENT_PAYLOAD" >"$HARNESS_STATE_DIR/agent-payload.txt"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        instructions = (self.harness_state / "instructions.txt").read_text(encoding="utf-8")
        fork_context = (self.harness_state / "fork-context.md").read_text(encoding="utf-8")
        payload = (self.harness_state / "agent-payload.txt").read_text(encoding="utf-8")

        self.assertIn("## Mission", instructions)
        self.assertIn("## Mission", fork_context)
        self.assertIn("## Mission", payload)
        self.assertNotIn("setup:", instructions)
        self.assertNotIn("setup:", fork_context)
        self.assertNotIn("setup:", payload)
        self.assertNotIn("---", instructions)
        self.assertNotIn("---", fork_context)
        self.assertNotIn("---", payload)

    def test_extra_run_instructions_are_appended_to_agent_visible_artifacts(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "---\nsetup: echo bootstrap-ok\n---\n## Mission\nKeep TV UX behavior.\n",
            encoding="utf-8",
        )
        _ = (self.harness_state / "extra-run-instructions.md").write_text(
            "".join(
                (
                    "## Extra Run Instructions\n\n",
                    "> This information was provided by the user with foreknowledge of what conflicts will occur in this rebase. You **MUST** follow any resolution decisions therein when the situation is encountered.\n\n",
                    "Resolve package-lock.json using upstream.\n\n",
                    "Keep fork-owned telemetry hooks intact.\n",
                )
            ),
            encoding="utf-8",
        )

        result = self._run_harness_shell(
            """
parse_fork_context
run_setup_command
write_instructions
build_agent_payload
printf '%s' "$AGENT_PAYLOAD" >"$HARNESS_STATE_DIR/agent-payload.txt"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        instructions = (self.harness_state / "instructions.txt").read_text(encoding="utf-8")
        fork_context = (self.harness_state / "fork-context.md").read_text(encoding="utf-8")
        payload = (self.harness_state / "agent-payload.txt").read_text(encoding="utf-8")

        self.assertIn("## Extra Run Instructions", instructions)
        self.assertIn(
            "This information was provided by the user with foreknowledge of what conflicts will occur in this rebase.",
            instructions,
        )
        self.assertIn("Resolve package-lock.json using upstream.", instructions)
        self.assertIn("Keep fork-owned telemetry hooks intact.", instructions)
        self.assertIn("## Extra Run Instructions", payload)
        self.assertNotIn("## Extra Run Instructions", fork_context)
        self.assertNotIn("setup:", payload)
        self.assertNotIn("---", payload)

    def test_main_skips_agent_when_initial_rebase_completes_cleanly(self) -> None:
        self._init_clean_rebase()

        result = self._run_harness_shell(
            f'''
HOME="{self.root / "home"}"
mkdir -p "$HOME"
OPENCODE_VARIANT=default
OPENCODE_AGENT=worker
launch_agent() {{
  printf 'called\n' >"$HARNESS_STATE_DIR/launch-agent.txt"
  return 0
}}
main
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse((self.harness_state / "launch-agent.txt").exists())
        self.assertFalse((self.harness_state / "instructions.txt").exists())
        status = (self.harness_state / "harness-status.txt").read_text(encoding="utf-8")
        self.assertIn("status=completed", status)
        self.assertIn("phase=rebase", status)
        self.assertIn("agent launch skipped", status)
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())
        self.assertEqual(
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", "upstream/main", "main"],
                cwd=self.workspace,
                check=False,
            ).returncode,
            0,
        )

    def test_main_launches_agent_when_initial_rebase_pauses_on_conflicts(self) -> None:
        self._init_conflicting_rebase()
        expected_sha = subprocess.run(
            ["git", "rev-parse", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        expected_subject = subprocess.run(
            ["git", "show", "-s", "--format=%s", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        result = self._run_harness_shell(
            f'''
HOME="{self.root / "home"}"
mkdir -p "$HOME"
OPENCODE_VARIANT=default
OPENCODE_AGENT=worker
launch_agent() {{
  printf 'called\n' >"$HARNESS_STATE_DIR/launch-agent.txt"
  return 0
}}
main
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((self.harness_state / "launch-agent.txt").exists())
        instructions = (self.harness_state / "instructions.txt").read_text(encoding="utf-8")
        self.assertIn("Forklift already started `git rebase upstream/main`", instructions)
        self.assertIn("git rebase --continue", instructions)
        self.assertNotIn("Run `git rebase upstream/main`", instructions)
        client_log = (self.harness_state / "opencode-client.log").read_text(encoding="utf-8")
        self.assertIn(
            f"[rebase] Recorded conflicting commit for {expected_sha} {expected_subject}",
            client_log,
        )
        self.assertIn("[rebase] Initial rebase paused on conflicts", client_log)
        self.assertEqual(
            (self.harness_state / "rebase-conflicting-commits.json").read_text(encoding="utf-8"),
            f"[\n  {{\n    \"sha\": \"{expected_sha}\",\n    \"subject\": \"{expected_subject}\"\n  }}\n]\n",
        )

    def test_main_fails_closed_when_initial_rebase_hard_fails(self) -> None:
        self._init_main_repo_without_upstream()

        result = self._run_harness_shell(
            f'''
HOME="{self.root / "home"}"
mkdir -p "$HOME"
OPENCODE_VARIANT=default
OPENCODE_AGENT=worker
launch_agent() {{
  printf 'called\n' >"$HARNESS_STATE_DIR/launch-agent.txt"
  return 0
}}
main
'''
        )

        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.harness_state / "launch-agent.txt").exists())
        self.assertFalse((self.harness_state / "instructions.txt").exists())
        status = (self.harness_state / "harness-status.txt").read_text(encoding="utf-8")
        self.assertIn("status=failed", status)
        self.assertIn("phase=rebase", status)
        self.assertIn("Initial rebase failed before agent launch", status)
        client_log = (self.harness_state / "opencode-client.log").read_text(encoding="utf-8")
        self.assertIn(
            "[rebase] Initial rebase failed before entering a paused rebase state",
            client_log,
        )

    def test_start_initial_rebase_sets_committer_identity_without_git_config(self) -> None:
        self._init_rebase_repo_without_configured_identity()

        result = self._run_harness_shell(
            f'''
HOME="{self.root / "home"}"
mkdir -p "$HOME"
resolve_real_git_bin
UPSTREAM_REF="upstream/main"
start_initial_rebase
printf '%s' "$INITIAL_REBASE_RESULT" >"$HARNESS_STATE_DIR/initial-rebase-result.txt"
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("Committer identity unknown", result.stderr)
        self.assertEqual(
            (self.harness_state / "initial-rebase-result.txt").read_text(encoding="utf-8"),
            "completed",
        )
        committer = subprocess.run(
            ["git", "log", "-1", "--format=%cn <%ce>"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(committer, "Forklift Agent <forklift@github.com>")

    def test_start_initial_rebase_auto_skips_clean_empty_stop_before_agent(self) -> None:
        fake_git, command_log = self._init_clean_empty_rebase_stop()

        result = self._run_harness_shell(
            f'''
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
UPSTREAM_REF="upstream/main"
start_initial_rebase
printf '%s' "$INITIAL_REBASE_RESULT" >"$HARNESS_STATE_DIR/initial-rebase-result.txt"
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            (self.harness_state / "initial-rebase-result.txt").read_text(encoding="utf-8"),
            "completed",
        )
        self.assertIn("[rebase] Auto-skipping clean empty rebase stop", result.stdout)
        self.assertIn(
            "[rebase] Initial rebase completed after auto-skipping clean empty stops",
            result.stdout,
        )
        self.assertNotIn("[rebase] Initial rebase paused on conflicts", result.stdout)
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())
        self.assertIn("-C " + str(self.workspace) + " rebase upstream/main", command_log.read_text(encoding="utf-8"))
        self.assertIn("-C " + str(self.workspace) + " rebase --skip", command_log.read_text(encoding="utf-8"))

    def test_handle_rebase_continue_auto_skips_clean_empty_stop_without_running_check(self) -> None:
        fake_git, command_log = self._init_clean_empty_rebase_stop()

        result = self._run_harness_shell(
            f'''
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo check-must-not-run
exit 7
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
handle_rebase_continue rebase --continue
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --continue", result.stdout)
        self.assertIn("[rebase] Auto-skipping clean empty rebase stop", result.stdout)
        self.assertNotIn("[rebase] Running frozen rebase continue check", result.stdout)
        self.assertNotIn("check-must-not-run", result.stderr)
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())
        self.assertIn("-C " + str(self.workspace) + " rebase --skip", command_log.read_text(encoding="utf-8"))

    def test_handle_rebase_skip_auto_skips_clean_empty_stop_without_rebase_head(self) -> None:
        fake_git, command_log = self._init_clean_empty_rebase_stop()

        result = self._run_harness_shell(
            f'''
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
initialize_rebase_skipped_commits_file
handle_rebase_skip rebase --skip
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --skip", result.stdout)
        self.assertIn("[rebase] Auto-skipping clean empty rebase stop", result.stdout)
        self.assertNotIn("Unable to determine REBASE_HEAD", result.stderr)
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())
        self.assertEqual(
            (self.harness_state / "rebase-skipped-commits.json").read_text(encoding="utf-8").strip(),
            "[]",
        )
        self.assertIn("-C " + str(self.workspace) + " rebase --skip", command_log.read_text(encoding="utf-8"))

    def test_handle_rebase_skip_still_fails_without_rebase_head_when_workspace_dirty(self) -> None:
        fake_git, _command_log = self._init_clean_empty_rebase_stop(dirty=True)

        result = self._run_harness_shell(
            f'''
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
if handle_rebase_skip rebase --skip; then
  echo "expected skip rejection" >&2
  exit 1
fi
'''
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --skip", result.stdout)
        self.assertIn("Unable to determine REBASE_HEAD for git rebase --skip", result.stderr)
        self.assertTrue((self.workspace / ".git" / "rebase-merge").exists())

    def test_git_wrapper_passes_through_normal_git_commands(self) -> None:
        self._init_workspace_repo()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
prepend_git_wrapper_path
cd "$WORKSPACE_DIR"
git status --short >/dev/null
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_git_wrapper_allows_read_only_configured_status_during_paused_rebase(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
prepend_git_wrapper_path
cd "$WORKSPACE_DIR"
git -c color.ui=always status --short >/dev/null
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("unsupported paused rebase command", result.stderr)

    def test_classify_paused_rebase_command_detects_continue_shape(self) -> None:
        result = self._run_harness_shell(
            """
classify_paused_rebase_command rebase --continue
printf '%s' "$PAUSED_REBASE_ACTION" >"$HARNESS_STATE_DIR/rebase-action.txt"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            (self.harness_state / "rebase-action.txt").read_text(encoding="utf-8"),
            "continue",
        )

    def test_git_wrapper_fails_closed_on_unsupported_paused_rebase_shape(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
prepend_git_wrapper_path
cd "$WORKSPACE_DIR"
if git -c color.ui=always rebase --continue; then
  echo "expected wrapper failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("unsupported paused rebase command", result.stderr)
        self.assertIn("Do not alter Git behavior or bypass the", result.stderr)
        self.assertIn("write STUCK.md", result.stderr)

    def test_git_wrapper_rejects_config_alias_bypass_during_paused_rebase(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
prepend_git_wrapper_path
cd "$WORKSPACE_DIR"
if git -c alias.stage='rebase --continue' stage; then
  echo "expected wrapper failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("unsupported paused rebase command", result.stderr)
        self.assertIn("Do not alter Git behavior or bypass the", result.stderr)
        self.assertIn("write STUCK.md", result.stderr)
        self.assertTrue((self.workspace / ".git" / "rebase-merge").exists())

    def test_git_wrapper_rejects_unapproved_command_during_paused_rebase(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
prepend_git_wrapper_path
cd "$WORKSPACE_DIR"
if git stage; then
  echo "expected wrapper failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("unsupported paused rebase command", result.stderr)
        self.assertIn("Do not alter Git behavior or bypass the", result.stderr)
        self.assertIn("write STUCK.md", result.stderr)
        self.assertTrue((self.workspace / ".git" / "rebase-merge").exists())

    def test_bash_subprocess_inherits_git_wrapper_for_paused_rebase(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
enable_rebase_mediation
cd "$WORKSPACE_DIR"
if bash -lc 'git -c color.ui=always rebase --continue'; then
  echo "expected wrapper failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("unsupported paused rebase command", result.stderr)
        self.assertIn("Do not alter Git behavior or bypass the", result.stderr)
        self.assertIn("write STUCK.md", result.stderr)

    def test_handle_rebase_continue_succeeds_after_passing_check(self) -> None:
        self._init_conflicting_rebase()
        expected_sha = subprocess.run(
            ["git", "rev-parse", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        expected_subject = subprocess.run(
            ["git", "show", "-s", "--format=%s", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
initialize_rebase_skipped_commits_file
initialize_rebase_conflicting_commits_file
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
true
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
printf 'merged\n' >"$WORKSPACE_DIR/tracked.txt"
git -C "$WORKSPACE_DIR" add tracked.txt
handle_rebase_continue rebase --continue
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --continue", result.stdout)
        self.assertIn(
            f"[rebase] Recorded conflicting commit for {expected_sha} {expected_subject}",
            result.stdout,
        )
        self.assertIn("[rebase] Running frozen rebase continue check", result.stdout)
        self.assertIn(
            "[rebase] Rebase continue check passed with stable workspace state",
            result.stdout,
        )
        self.assertIn("[rebase] Invoking real git rebase --continue", result.stdout)
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())
        self.assertEqual(
            (self.harness_state / "rebase-conflicting-commits.json").read_text(encoding="utf-8"),
            f"[\n  {{\n    \"sha\": \"{expected_sha}\",\n    \"subject\": \"{expected_subject}\"\n  }}\n]\n",
        )

    def test_handle_rebase_continue_blocks_on_non_zero_check(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo check-failed
exit 7
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
printf 'merged\n' >"$WORKSPACE_DIR/tracked.txt"
git -C "$WORKSPACE_DIR" add tracked.txt
if handle_rebase_continue rebase --continue; then
  echo "expected continue failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --continue", result.stdout)
        self.assertIn("[rebase] Running frozen rebase continue check", result.stdout)
        self.assertIn("Rebase continue check failed.", result.stderr)
        self.assertIn("check-failed", result.stderr)
        self.assertTrue((self.workspace / ".git" / "rebase-merge").exists())

    def test_handle_rebase_continue_blocks_on_tracked_mutation(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'mutated\n' > tracked.txt
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
printf 'merged\n' >"$WORKSPACE_DIR/tracked.txt"
git -C "$WORKSPACE_DIR" add tracked.txt
if handle_rebase_continue rebase --continue; then
  echo "expected tracked-mutation failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Rebase continue check changed workspace state.", result.stderr)
        self.assertIn("tracked.txt", result.stderr)

    def test_handle_rebase_continue_blocks_on_untracked_mutation(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
touch new-file.txt
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
printf 'merged\n' >"$WORKSPACE_DIR/tracked.txt"
git -C "$WORKSPACE_DIR" add tracked.txt
if handle_rebase_continue rebase --continue; then
  echo "expected untracked-mutation failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Rebase continue check changed workspace state.", result.stderr)
        self.assertIn("?? new-file.txt", result.stderr)

    def test_handle_rebase_continue_can_retry_after_fixing_check(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
printf 'merged\n' >"$WORKSPACE_DIR/tracked.txt"
git -C "$WORKSPACE_DIR" add tracked.txt
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 9
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
if handle_rebase_continue rebase --continue; then
  echo "expected initial continue failure" >&2
  exit 1
fi
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
true
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
handle_rebase_continue rebase --continue
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())

    def test_handle_rebase_continue_dedupes_conflicting_commit_retries(self) -> None:
        self._init_conflicting_rebase()
        expected_sha = subprocess.run(
            ["git", "rev-parse", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        expected_subject = subprocess.run(
            ["git", "show", "-s", "--format=%s", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
initialize_rebase_conflicting_commits_file
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 9
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
if handle_rebase_continue rebase --continue; then
  echo "expected initial continue failure" >&2
  exit 1
fi
if handle_rebase_continue rebase --continue; then
  echo "expected retry continue failure" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            (self.harness_state / "rebase-conflicting-commits.json").read_text(encoding="utf-8"),
            f"[\n  {{\n    \"sha\": \"{expected_sha}\",\n    \"subject\": \"{expected_subject}\"\n  }}\n]\n",
        )

    def test_handle_rebase_continue_auto_skips_resolved_redundant_commit(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
initialize_rebase_skipped_commits_file
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
true
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
printf 'upstream\n' >"$WORKSPACE_DIR/tracked.txt"
git -C "$WORKSPACE_DIR" add tracked.txt
handle_rebase_continue rebase --continue
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --continue", result.stdout)
        self.assertIn("[rebase] Auto-skipping clean empty rebase stop", result.stdout)
        self.assertNotIn("[rebase] Running frozen rebase continue check", result.stdout)
        self.assertNotIn("[rebase] Invoking real git rebase --continue", result.stdout)
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())
        self.assertEqual(
            (self.harness_state / "rebase-skipped-commits.json").read_text(encoding="utf-8").strip(),
            "[]",
        )

    def test_handle_rebase_continue_auto_skips_clean_empty_commit_before_check(self) -> None:
        self._init_conflicting_rebase()
        fake_git = self.harness_state / "fake-git.sh"
        skip_marker = self.harness_state / "skip-called.txt"
        rebase_merge = self.workspace / ".git" / "rebase-merge"
        _ = fake_git.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    'if [[ "$1" == "-C" ]]; then',
                    '  workspace_dir="$2"',
                    '  shift 2',
                    "else",
                    '  workspace_dir=""',
                    "fi",
                    'if [[ "$1" == "status" ]]; then',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rev-parse" && "$2" == "REBASE_HEAD" ]]; then',
                    '  git -C "$workspace_dir" rev-parse REBASE_HEAD',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "show" && "$2" == "-s" && "$3" == "--format=%s" && "$4" == "REBASE_HEAD" ]]; then',
                    '  git -C "$workspace_dir" show -s --format=%s REBASE_HEAD',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rebase" && "$2" == "--continue" ]]; then',
                    "  exit 1",
                    "fi",
                    'if [[ "$1" == "rebase" && "$2" == "--skip" ]]; then',
                    f'  printf skip-called >"{skip_marker}"',
                    f'  rm -rf "{rebase_merge}"',
                    "  exit 0",
                    "fi",
                    'printf "unexpected fake git args: %s\\n" "$*" >&2',
                    "exit 99",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        result = self._run_harness_shell(
            f"""
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
initialize_rebase_skipped_commits_file
initialize_rebase_conflicting_commits_file
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo check-must-not-run
exit 7
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
handle_rebase_continue rebase --continue
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --continue", result.stdout)
        self.assertIn("[rebase] Auto-skipping clean empty rebase stop", result.stdout)
        self.assertNotIn("[rebase] Running frozen rebase continue check", result.stdout)
        self.assertNotIn("[rebase] Invoking real git rebase --continue", result.stdout)
        self.assertNotIn("check-must-not-run", result.stderr)
        self.assertEqual(skip_marker.read_text(encoding="utf-8"), "skip-called")
        self.assertFalse(rebase_merge.exists())
        self.assertEqual(
            (self.harness_state / "rebase-skipped-commits.json").read_text(encoding="utf-8").strip(),
            "[]",
        )
        self.assertEqual(
            (self.harness_state / "rebase-conflicting-commits.json").read_text(encoding="utf-8").strip(),
            "[]",
        )

    def test_handle_rebase_skip_records_rebase_head_identity(self) -> None:
        self._init_conflicting_rebase()
        expected_sha = subprocess.run(
            ["git", "rev-parse", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        expected_subject = subprocess.run(
            ["git", "show", "-s", "--format=%s", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
initialize_rebase_skipped_commits_file
handle_rebase_skip rebase --skip
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --skip", result.stdout)
        self.assertIn(
            f"[rebase] Recorded explicit skip for {expected_sha} {expected_subject}",
            result.stdout,
        )
        self.assertEqual(
            (self.harness_state / "rebase-skipped-commits.json").read_text(encoding="utf-8"),
            f"[\n  {{\n    \"sha\": \"{expected_sha}\",\n    \"subject\": \"{expected_subject}\"\n  }}\n]\n",
        )

    def test_handle_rebase_abort_rejects_missing_or_blank_stuck(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
if handle_rebase_abort rebase --abort; then
  echo "expected abort rejection" >&2
  exit 1
fi
printf '   \n' >"$WORKSPACE_DIR/STUCK.md"
if handle_rebase_abort rebase --abort; then
  echo "expected blank abort rejection" >&2
  exit 1
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.count("[rebase] Intercepted git rebase --abort"), 2)
        self.assertIn("Cannot abort rebase until STUCK.md explains what blocked progress.", result.stderr)
        self.assertTrue((self.workspace / ".git" / "rebase-merge").exists())

    def test_handle_rebase_abort_allows_non_empty_stuck(self) -> None:
        self._init_conflicting_rebase()

        result = self._run_harness_shell(
            """
resolve_real_git_bin
printf 'Need help\n' >"$WORKSPACE_DIR/STUCK.md"
handle_rebase_abort rebase --abort
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[rebase] Intercepted git rebase --abort", result.stdout)
        self.assertIn(
            "[rebase] Allowing git rebase --abort because STUCK.md is present",
            result.stdout,
        )
        self.assertFalse((self.workspace / ".git" / "rebase-merge").exists())

    def test_emit_rebase_event_from_snapshot_uses_merge_backend_progress(self) -> None:
        self._init_conflicting_rebase()
        expected_sha = subprocess.run(
            ["git", "rev-parse", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        expected_subject = subprocess.run(
            ["git", "show", "-s", "--format=%s", "REBASE_HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
resolve_real_git_bin
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
emit_rebase_event_from_snapshot conflict
'''
            )
            server.wait_for_event_count(1)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(server.events[0]["event"], "conflict")
        self.assertEqual(server.events[0]["step"], 1)
        self.assertEqual(server.events[0]["total"], 1)
        self.assertEqual(server.events[0]["sha"], expected_sha)
        self.assertEqual(server.events[0]["subject"], expected_subject)
        self.assertEqual(server.events[0]["files"], ["tracked.txt"])

    def test_emit_rebase_event_from_snapshot_supports_apply_backend(self) -> None:
        git_dir = self.workspace / ".git" / "rebase-apply"
        git_dir.mkdir(parents=True, exist_ok=True)
        _ = (git_dir / "next").write_text("7\n", encoding="utf-8")
        _ = (git_dir / "last").write_text("9\n", encoding="utf-8")
        fake_git = self.harness_state / "fake-git.sh"
        _ = fake_git.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    'if [[ "$1" == "-C" ]]; then',
                    '  shift 2',
                    "fi",
                    'if [[ "$1" == "rev-parse" && "$2" == "REBASE_HEAD" ]]; then',
                    "  printf '1234567890abcdef1234567890abcdef12345678\\n'",
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "show" && "$2" == "-s" && "$3" == "--format=%s" && "$4" == "REBASE_HEAD" ]]; then',
                    "  printf 'Apply backend commit\\n'",
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "diff" && "$2" == "--name-only" && "$3" == "--diff-filter=U" ]]; then',
                    "  printf 'src/apply.py\\n'",
                    "  printf 'tests/test_apply.py\\n'",
                    "  exit 0",
                    "fi",
                    'printf "unexpected fake git args: %s\\n" "$*" >&2',
                    "exit 99",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
emit_rebase_event_from_snapshot progress
'''
            )
            server.wait_for_event_count(1)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            server.events,
            [
                {
                    "v": 1,
                    "event": "progress",
                    "step": 7,
                    "total": 9,
                    "sha": "1234567890abcdef1234567890abcdef12345678",
                    "subject": "Apply backend commit",
                    "files": ["src/apply.py", "tests/test_apply.py"],
                }
            ],
        )

    def test_emit_rebase_event_ignores_missing_or_unreachable_socket(self) -> None:
        self._init_conflicting_rebase()

        missing_socket_result = self._run_harness_shell(
            """
resolve_real_git_bin
emit_rebase_event_from_snapshot conflict
"""
        )
        self.assertEqual(missing_socket_result.returncode, 0, msg=missing_socket_result.stderr)

        unreachable_socket = self.root / "missing" / "rebase-events.sock"
        unreachable_result = self._run_harness_shell(
            f'''
resolve_real_git_bin
export FORKLIFT_REBASE_EVENTS_SOCK="{unreachable_socket}"
emit_rebase_event_from_snapshot conflict
'''
        )
        self.assertEqual(unreachable_result.returncode, 0, msg=unreachable_result.stderr)
        self.assertIn(
            "Unable to emit structured rebase event conflict",
            unreachable_result.stderr,
        )

    def test_main_emits_progress_and_conflict_events_for_initial_pause(self) -> None:
        self._init_conflicting_rebase()
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
HOME="{self.root / "home"}"
mkdir -p "$HOME"
OPENCODE_VARIANT=default
OPENCODE_AGENT=worker
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
launch_agent() {{
  return 0
}}
main
'''
            )
            server.wait_for_event_count(2)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual([event["event"] for event in server.events], ["progress", "conflict"])

    def test_main_emits_complete_event_for_clean_initial_rebase(self) -> None:
        self._init_clean_rebase()
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
HOME="{self.root / "home"}"
mkdir -p "$HOME"
OPENCODE_VARIANT=default
OPENCODE_AGENT=worker
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
launch_agent() {{
  return 0
}}
main
'''
            )
            server.wait_for_event_count(1)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(server.events, [{"v": 1, "event": "complete", "step": 1, "total": 1}])

    def test_handle_rebase_continue_emits_continue_then_repeated_conflict(self) -> None:
        self._init_conflicting_rebase()
        fake_git = self.harness_state / "fake-git.sh"
        _ = fake_git.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    'if [[ "$1" == "-C" ]]; then',
                    '  workspace_dir="$2"',
                    '  shift 2',
                    "else",
                    '  workspace_dir=""',
                    "fi",
                    'if [[ "$1" == "status" ]]; then',
                    '  printf "UU tracked.txt\\n"',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rev-parse" && "$2" == "REBASE_HEAD" ]]; then',
                    '  git -C "$workspace_dir" rev-parse REBASE_HEAD',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "show" && "$2" == "-s" && "$3" == "--format=%s" && "$4" == "REBASE_HEAD" ]]; then',
                    '  git -C "$workspace_dir" show -s --format=%s REBASE_HEAD',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "diff" && "$2" == "--name-only" && "$3" == "--diff-filter=U" ]]; then',
                    '  printf tracked.txt\\n',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rebase" && "$2" == "--continue" ]]; then',
                    "  exit 1",
                    "fi",
                    'printf "unexpected fake git args: %s\\n" "$*" >&2',
                    "exit 99",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
initialize_rebase_conflicting_commits_file
handle_rebase_continue rebase --continue || true
'''
            )
            server.wait_for_event_count(3)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            [event["event"] for event in server.events],
            ["continue", "progress", "conflict"],
        )

    def test_handle_rebase_skip_emits_skip_and_complete_events(self) -> None:
        self._init_conflicting_rebase()
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
resolve_real_git_bin
initialize_rebase_skipped_commits_file
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
handle_rebase_skip rebase --skip
'''
            )
            server.wait_for_event_count(2)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual([event["event"] for event in server.events], ["skip", "complete"])

    def test_handle_rebase_continue_emits_auto_skip_sequence(self) -> None:
        self._init_conflicting_rebase()
        fake_git = self.harness_state / "fake-git.sh"
        skip_marker = self.harness_state / "skip-called.txt"
        rebase_merge = self.workspace / ".git" / "rebase-merge"
        _ = fake_git.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    'if [[ "$1" == "-C" ]]; then',
                    '  workspace_dir="$2"',
                    '  shift 2',
                    "else",
                    '  workspace_dir=""',
                    "fi",
                    'if [[ "$1" == "status" ]]; then',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rev-parse" && "$2" == "REBASE_HEAD" ]]; then',
                    '  git -C "$workspace_dir" rev-parse REBASE_HEAD',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "show" && "$2" == "-s" && "$3" == "--format=%s" && "$4" == "REBASE_HEAD" ]]; then',
                    '  git -C "$workspace_dir" show -s --format=%s REBASE_HEAD',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "diff" && "$2" == "--name-only" && "$3" == "--diff-filter=U" ]]; then',
                    '  printf tracked.txt\\n',
                    "  exit 0",
                    "fi",
                    'if [[ "$1" == "rebase" && "$2" == "--continue" ]]; then',
                    "  exit 1",
                    "fi",
                    'if [[ "$1" == "rebase" && "$2" == "--skip" ]]; then',
                    f'  printf skip-called >"{skip_marker}"',
                    f'  rm -rf "{rebase_merge}"',
                    "  exit 0",
                    "fi",
                    'printf "unexpected fake git args: %s\\n" "$*" >&2',
                    "exit 99",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
REAL_GIT_BIN="{fake_git}"
export REAL_GIT_BIN
initialize_rebase_conflicting_commits_file
cat >"$REBASE_CONTINUE_CHECK_FILE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
true
EOF
chmod +x "$REBASE_CONTINUE_CHECK_FILE"
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
handle_rebase_continue rebase --continue
'''
            )
            server.wait_for_event_count(2)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(skip_marker.read_text(encoding="utf-8"), "skip-called")
        self.assertEqual(
            [event["event"] for event in server.events],
            ["auto_skip", "complete"],
        )

    def test_handle_rebase_abort_emits_abort_event(self) -> None:
        self._init_conflicting_rebase()
        socket_path = self._event_socket_path()

        with RebaseEventServer(socket_path) as server:
            result = self._run_harness_shell(
                f'''
resolve_real_git_bin
printf 'Need help\n' >"$WORKSPACE_DIR/STUCK.md"
export FORKLIFT_REBASE_EVENTS_SOCK="{socket_path}"
handle_rebase_abort rebase --abort
'''
            )
            server.wait_for_event_count(1)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(server.events[0]["event"], "abort")


if __name__ == "__main__":
    _ = unittest.main()
