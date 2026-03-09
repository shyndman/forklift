from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

HARNESS_SCRIPT = Path(__file__).resolve().parents[1] / "docker/kitchen-sink/harness/run.sh"


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
WORKSPACE_DIR=\"{self.workspace}\"
HARNESS_STATE_DIR=\"{self.harness_state}\"
INSTRUCTIONS_FILE=\"$HARNESS_STATE_DIR/instructions.txt\"
FORK_CONTEXT_FILE=\"$HARNESS_STATE_DIR/fork-context.md\"
SETUP_LOG=\"$HARNESS_STATE_DIR/setup.log\"
CLIENT_LOG=\"$HARNESS_STATE_DIR/opencode-client.log\"
: >\"$CLIENT_LOG\"
: >\"$SETUP_LOG\"
{commands}
"""
        return subprocess.run(
            ["bash", "-lc", script],
            text=True,
            capture_output=True,
            check=False,
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

    def test_setup_and_changelog_metadata_are_both_supported(self) -> None:
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
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        excludes = (self.harness_state / "changelog-excludes.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(excludes, "data/big-snapshot.json\n!data/keep.json")
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

    def test_setup_success_writes_setup_log(self) -> None:
        self._init_workspace_repo()
        _ = (self.workspace / "FORK.md").write_text(
            "---\nsetup: echo bootstrap-ok\n---\n## Mission\nPreserve custom behavior.\n",
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
        self.assertIn("bootstrap-ok", setup_log)

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


if __name__ == "__main__":
    _ = unittest.main()
