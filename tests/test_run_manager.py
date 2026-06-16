from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import json
from typing import cast, override
from unittest.mock import patch

from forklift.run_manager import EXTRA_RUN_INSTRUCTIONS_FILE_NAME, RunDirectoryManager


class OverlayRunDirectoryManager(RunDirectoryManager):
    def overlay_fork_context(self, source_repo: Path, workspace: Path) -> None:
        self._overlay_fork_context(source_repo, workspace)


class PrepareRunDirectoryManager(RunDirectoryManager):
    @override
    def _clone_repo(self, source: Path, destination: Path) -> None:
        _ = source
        destination.mkdir(parents=True)
        _ = (destination / ".git").mkdir()

    @override
    def _capture_branch_info(
        self, source_repo: Path, main_branch: str
    ) -> dict[str, str | None]:
        _ = source_repo
        return {
            "main_branch": main_branch,
            "origin_main_sha": "origin-sha",
            "upstream_main_sha": "upstream-sha",
        }

    @override
    def _remove_remotes(self, workspace: Path) -> None:
        _ = workspace

    @override
    def _seed_upstream_ref(
        self, workspace: Path, upstream_sha: str | None, main_branch: str
    ) -> None:
        _ = (workspace, upstream_sha, main_branch)

    @override
    def _generate_run_id(self) -> str:
        return "RID1"


class RunManagerForkContextTests(unittest.TestCase):
    def test_overlay_copies_repo_root_fork_md(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            workspace = root / "workspace"
            source_repo.mkdir()
            workspace.mkdir()
            expected = "root context\n"
            _ = (source_repo / "FORK.md").write_text(expected, encoding="utf-8")

            OverlayRunDirectoryManager().overlay_fork_context(source_repo, workspace)

            self.assertEqual(
                (workspace / "FORK.md").read_text(encoding="utf-8"), expected
            )

    def test_overlay_falls_back_to_agents_fork_md(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            workspace = root / "workspace"
            agents_dir = source_repo / ".agents"
            agents_dir.mkdir(parents=True)
            workspace.mkdir()
            expected = "agents context\n"
            _ = (agents_dir / "FORK.md").write_text(expected, encoding="utf-8")

            OverlayRunDirectoryManager().overlay_fork_context(source_repo, workspace)

            self.assertEqual(
                (workspace / "FORK.md").read_text(encoding="utf-8"), expected
            )

    def test_overlay_prefers_repo_root_fork_md_over_agents_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            workspace = root / "workspace"
            agents_dir = source_repo / ".agents"
            source_repo.mkdir()
            workspace.mkdir()
            agents_dir.mkdir()
            _ = (source_repo / "FORK.md").write_text("root context\n", encoding="utf-8")
            _ = (agents_dir / "FORK.md").write_text(
                "agents context\n", encoding="utf-8"
            )

            OverlayRunDirectoryManager().overlay_fork_context(source_repo, workspace)

            self.assertEqual(
                (workspace / "FORK.md").read_text(encoding="utf-8"),
                "root context\n",
            )

    def test_prepare_creates_control_dir_and_aligns_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            source_repo.mkdir()
            manager = PrepareRunDirectoryManager(runs_root=root / "runs")

            with patch(
                "forklift.run_manager.os.chown", return_value=None
            ) as chown_mock:
                run_paths = manager.prepare(source_repo)

            self.assertEqual(run_paths.control_dir, run_paths.run_dir / "control")
            self.assertTrue(run_paths.control_dir.is_dir())
            self.assertTrue(run_paths.control_dir.exists())
            self.assertTrue(run_paths.control_dir.stat().st_mode)

            chowned_paths = {
                Path(cast(str, call.args[0])) for call in chown_mock.call_args_list
            }
            self.assertIn(run_paths.control_dir, chowned_paths)
            self.assertIn(run_paths.workspace, chowned_paths)
            self.assertIn(run_paths.harness_state, chowned_paths)

    def test_prepare_persists_extra_run_instructions_for_harness_and_metadata(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_repo = root / "source"
            source_repo.mkdir()
            manager = PrepareRunDirectoryManager(runs_root=root / "runs")

            with patch("forklift.run_manager.os.chown", return_value=None):
                run_paths = manager.prepare(
                    source_repo,
                    extra_instructions=(
                        "Resolve package-lock.json using upstream.",
                        "Keep fork-owned telemetry hooks intact.",
                    ),
                )

            extra_file = run_paths.harness_state / EXTRA_RUN_INSTRUCTIONS_FILE_NAME
            self.assertEqual(
                extra_file.read_text(encoding="utf-8"),
                "".join(
                    (
                        "## Extra Run Instructions\n\n",
                        "> This information was provided by the user with foreknowledge of what conflicts will occur in this rebase. You **MUST** follow any resolution decisions therein when the situation is encountered.\n\n",
                        "Resolve package-lock.json using upstream.\n\n",
                        "Keep fork-owned telemetry hooks intact.\n",
                    )
                ),
            )

            metadata = cast(
                dict[str, object],
                json.loads(
                    (run_paths.run_dir / "metadata.json").read_text(encoding="utf-8")
                ),
            )
            self.assertEqual(
                metadata["extra_instructions"],
                [
                    "Resolve package-lock.json using upstream.",
                    "Keep fork-owned telemetry hooks intact.",
                ],
            )


if __name__ == "__main__":
    _ = unittest.main()
