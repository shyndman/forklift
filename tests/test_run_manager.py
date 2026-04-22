from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from forklift.run_manager import RunDirectoryManager


class OverlayRunDirectoryManager(RunDirectoryManager):
    def overlay_fork_context(self, source_repo: Path, workspace: Path) -> None:
        self._overlay_fork_context(source_repo, workspace)


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

            self.assertEqual((workspace / "FORK.md").read_text(encoding="utf-8"), expected)

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

            self.assertEqual((workspace / "FORK.md").read_text(encoding="utf-8"), expected)

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
            _ = (agents_dir / "FORK.md").write_text("agents context\n", encoding="utf-8")

            OverlayRunDirectoryManager().overlay_fork_context(source_repo, workspace)

            self.assertEqual(
                (workspace / "FORK.md").read_text(encoding="utf-8"),
                "root context\n",
            )


if __name__ == "__main__":
    _ = unittest.main()
