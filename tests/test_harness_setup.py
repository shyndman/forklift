from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

HARNESS_SCRIPT = (
    Path(__file__).resolve().parents[1] / "docker/kitchen-sink/harness/run.sh"
)


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
HARNESS_STATUS_FILE="$HARNESS_STATE_DIR/harness-status.txt"
REBASE_CONTINUE_CHECK_FILE="$HARNESS_STATE_DIR/rebase-continue-check.sh"
REBASE_SKIPPED_COMMITS_FILE="$HARNESS_STATE_DIR/rebase-skipped-commits.json"
REBASE_CONFLICTING_COMMITS_FILE="$HARNESS_STATE_DIR/rebase-conflicting-commits.json"
export WORKSPACE_DIR HARNESS_STATE_DIR INSTRUCTIONS_FILE FORK_CONTEXT_FILE EXTRA_RUN_INSTRUCTIONS_FILE SETUP_LOG HARNESS_STATUS_FILE
export REBASE_CONTINUE_CHECK_FILE REBASE_SKIPPED_COMMITS_FILE REBASE_CONFLICTING_COMMITS_FILE
: >"$SETUP_LOG"
{commands}
"""
        return subprocess.run(
            ["bash", "-lc", script],
            text=True,
            capture_output=True,
            check=False,
        )

    def _run_git_with_commit_identity(
        self, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
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
        _ = subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=self.workspace,
            check=True,
        )

    def _init_conflicting_rebase(self) -> None:
        _ = subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.workspace, check=True
        )
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
        _ = subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "commit", "-m", "base"], cwd=self.workspace, check=True
        )
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        _ = tracked.write_text("fork\n", encoding="utf-8")
        _ = subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "commit", "-m", "fork change"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "branch", "upstream/main", base_sha], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "checkout", "upstream/main"], cwd=self.workspace, check=True
        )
        _ = tracked.write_text("upstream\n", encoding="utf-8")
        _ = subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.workspace, check=True
        )
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
        _ = subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.workspace, check=True
        )
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
        _ = subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "commit", "-m", "base"], cwd=self.workspace, check=True
        )
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

        fork_only = self.workspace / "fork-only.txt"
        _ = fork_only.write_text("fork\n", encoding="utf-8")
        _ = subprocess.run(
            ["git", "add", "fork-only.txt"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "commit", "-m", "fork change"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "branch", "upstream/main", base_sha], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "checkout", "upstream/main"], cwd=self.workspace, check=True
        )
        upstream_only = self.workspace / "upstream-only.txt"
        _ = upstream_only.write_text("upstream\n", encoding="utf-8")
        _ = subprocess.run(
            ["git", "add", "upstream-only.txt"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "commit", "-m", "upstream change"],
            cwd=self.workspace,
            check=True,
        )
        _ = subprocess.run(["git", "checkout", "main"], cwd=self.workspace, check=True)

    def _init_rebase_repo_without_configured_identity(self) -> None:
        _ = subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.workspace, check=True
        )

        tracked = self.workspace / "tracked.txt"
        _ = tracked.write_text("base\n", encoding="utf-8")
        _ = subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.workspace, check=True
        )
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
        _ = subprocess.run(
            ["git", "add", "fork-only.txt"], cwd=self.workspace, check=True
        )
        _ = self._run_git_with_commit_identity(["commit", "-m", "fork change"])
        _ = subprocess.run(
            ["git", "branch", "upstream/main", base_sha], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "checkout", "upstream/main"], cwd=self.workspace, check=True
        )
        upstream_only = self.workspace / "upstream-only.txt"
        _ = upstream_only.write_text("upstream\n", encoding="utf-8")
        _ = subprocess.run(
            ["git", "add", "upstream-only.txt"], cwd=self.workspace, check=True
        )
        _ = self._run_git_with_commit_identity(["commit", "-m", "upstream change"])
        _ = subprocess.run(["git", "checkout", "main"], cwd=self.workspace, check=True)

    def _init_main_repo_without_upstream(self) -> None:
        _ = subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.workspace, check=True
        )
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
        _ = subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.workspace, check=True
        )
        _ = subprocess.run(
            ["git", "commit", "-m", "base"], cwd=self.workspace, check=True
        )

    def _init_clean_empty_rebase_stop(
        self, *, dirty: bool = False
    ) -> tuple[Path, Path]:
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

    def test_parse_fork_context_without_front_matter_treats_whole_file_as_body(
        self,
    ) -> None:
        fork_file = self.workspace / "FORK.md"
        _ = fork_file.write_text(
            "## Mission\nKeep behavior stable.\n", encoding="utf-8"
        )

        result = self._run_harness_shell(
            """
parse_fork_context
printf '%s' "$FORK_SETUP_COMMAND" >"$HARNESS_STATE_DIR/setup-command.txt"
printf '%s' "$FORK_CONTEXT_BODY" >"$HARNESS_STATE_DIR/fork-body.txt"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            (self.harness_state / "setup-command.txt").read_text(encoding="utf-8"), ""
        )
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

    def test_parse_fork_context_fails_closed_on_invalid_changelog_exclude_entry(
        self,
    ) -> None:
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
        self.assertIn(
            "changelog.exclude entries must be non-empty strings", result.stderr
        )

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

    def test_rebase_continue_check_snapshot_stays_frozen_after_fork_md_changes(
        self,
    ) -> None:
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
        self.assertIn("setup-stdout", setup_log)
        self.assertIn("setup-stderr", setup_log)
        self.assertIn("[setup] setup-stdout", result.stdout)
        self.assertIn("[setup] setup-stderr", result.stderr)
        # Each setup line is tagged exactly once on its own stream: no double
        # prefix, and stderr is not cross-routed onto stdout.
        self.assertNotIn("[setup] [setup]", result.stdout)
        self.assertNotIn("[setup] [setup]", result.stderr)
        self.assertEqual(result.stdout.count("setup-stdout"), 1)
        self.assertNotIn("setup-stderr", result.stdout)
        self.assertEqual(result.stderr.count("setup-stderr"), 1)

    def test_configure_git_lfs_filters_installs_global_filters_when_available(
        self,
    ) -> None:
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
                    'if [[ "${1:-}" == "lfs" && "${2:-}" == "version" ]]; then\n',
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
        self.assertIn("Configuring Git LFS filters", result.stdout)
        self.assertIn("git-lfs=git-lfs/9.9.9", result.stdout)

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
        self.assertIn("Tracked Changes After Setup", result.stdout)
        self.assertIn("tracked.txt", result.stdout)

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
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        instructions = (self.harness_state / "instructions.txt").read_text(
            encoding="utf-8"
        )
        fork_context = (self.harness_state / "fork-context.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("## Mission", instructions)
        self.assertIn("## Mission", fork_context)
        self.assertNotIn("setup:", instructions)
        reminder = (
            'In the context of this rebase, "ours" refers to the upstream project'
        )
        self.assertIn(reminder, instructions)
        self.assertNotIn("setup:", fork_context)
        self.assertNotIn("---", instructions)
        self.assertNotIn("---", fork_context)

    def test_extra_run_instructions_are_appended_to_agent_visible_artifacts(
        self,
    ) -> None:
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
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        instructions = (self.harness_state / "instructions.txt").read_text(
            encoding="utf-8"
        )
        fork_context = (self.harness_state / "fork-context.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("## Extra Run Instructions", instructions)
        self.assertIn(
            "This information was provided by the user with foreknowledge of what conflicts will occur in this rebase.",
            instructions,
        )
        self.assertIn("Resolve package-lock.json using upstream.", instructions)
        self.assertIn("Keep fork-owned telemetry hooks intact.", instructions)
        self.assertNotIn("## Extra Run Instructions", fork_context)


if __name__ == "__main__":
    _ = unittest.main()
