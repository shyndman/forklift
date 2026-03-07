from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from forklift.changelog import Changelog
from forklift.changelog_analysis import (
    ChangelogAnalysisError,
    MergeTreeResult,
    build_evidence_bundle,
    parse_merge_tree_conflict_hotspots,
    parse_name_status_output,
    parse_numstat_output,
    resolve_merge_tree_hotspots,
)
from forklift.changelog_llm import ChangelogLlmError
from forklift.changelog_models import (
    ChangedFileStat,
    ConflictHotspot,
    DiffSummary,
    EvidenceBundle,
)
from forklift.cli import Forklift
from forklift.opencode_env import OpenCodeEnv


class ChangelogCliParsingTests(unittest.TestCase):
    def test_forklift_parse_routes_changelog_subcommand(self) -> None:
        command = Forklift.parse(["changelog"])
        self.assertIsInstance(command.subcommand, Changelog)


class ChangelogModelTests(unittest.TestCase):
    def test_dataclass_defaults_and_field_types(self) -> None:
        hotspot = ConflictHotspot(path="src/app.py")
        summary = DiffSummary()
        file_stat = ChangedFileStat(path="src/app.py")
        evidence = EvidenceBundle(
            base_sha="abc123",
            main_branch="main",
            upstream_ref="upstream/main",
        )

        self.assertEqual(hotspot.conflict_count, 1)
        self.assertEqual(summary.files_changed, 0)
        self.assertEqual(summary.insertions, 0)
        self.assertEqual(summary.deletions, 0)
        self.assertEqual(file_stat.status, "M")
        self.assertEqual(file_stat.added, 0)
        self.assertEqual(file_stat.removed, 0)

        self.assertIsInstance(evidence.conflicts, list)
        self.assertIsInstance(evidence.diff_summary, DiffSummary)
        self.assertIsInstance(evidence.top_changed_files, list)
        self.assertIsInstance(evidence.important_notes, list)


class ChangelogAnalysisTests(unittest.TestCase):
    def test_parse_merge_tree_conflicts_no_conflicts(self) -> None:
        output = "f00ba47f00ba47f00ba47f00ba47f00ba47f00ba"
        self.assertEqual(parse_merge_tree_conflict_hotspots(output), [])

    def test_parse_merge_tree_conflicts_counts_multiple_blocks_for_one_file(
        self,
    ) -> None:
        output = "\n".join(
            [
                "beadbeadbeadbeadbeadbeadbeadbeadbeadbead",
                "100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 src/conflict.py",
                "100644 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 src/conflict.py",
                "100644 cccccccccccccccccccccccccccccccccccccccc 3 src/conflict.py",
                "100644 dddddddddddddddddddddddddddddddddddddddd 1 src/conflict.py",
                "100644 eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee 2 src/conflict.py",
                "100644 ffffffffffffffffffffffffffffffffffffffff 3 src/conflict.py",
            ]
        )

        hotspots = parse_merge_tree_conflict_hotspots(output)
        self.assertEqual(len(hotspots), 1)
        self.assertEqual(hotspots[0].path, "src/conflict.py")
        self.assertEqual(hotspots[0].conflict_count, 2)

    def test_parse_merge_tree_conflicts_multiple_files(self) -> None:
        output = "\n".join(
            [
                "beadbeadbeadbeadbeadbeadbeadbeadbeadbead",
                "100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 src/alpha.py",
                "100644 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 src/alpha.py",
                "100644 cccccccccccccccccccccccccccccccccccccccc 3 src/alpha.py",
                "100644 dddddddddddddddddddddddddddddddddddddddd 1 src/beta.py",
                "100644 eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee 2 src/beta.py",
                "100644 ffffffffffffffffffffffffffffffffffffffff 3 src/beta.py",
                "100644 1111111111111111111111111111111111111111 1 src/beta.py",
                "100644 2222222222222222222222222222222222222222 2 src/beta.py",
                "100644 3333333333333333333333333333333333333333 3 src/beta.py",
            ]
        )

        hotspots = parse_merge_tree_conflict_hotspots(output)
        self.assertEqual(
            [(item.path, item.conflict_count) for item in hotspots],
            [("src/beta.py", 2), ("src/alpha.py", 1)],
        )

    def test_merge_tree_exit_zero_returns_no_hotspots(self) -> None:
        result = MergeTreeResult(exit_code=0, output="treeoid")
        self.assertEqual(resolve_merge_tree_hotspots(result), [])

    def test_merge_tree_exit_one_parses_hotspots(self) -> None:
        output = "\n".join(
            [
                "treeoid",
                "100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 src/conflict.py",
                "100644 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 src/conflict.py",
                "100644 cccccccccccccccccccccccccccccccccccccccc 3 src/conflict.py",
            ]
        )
        result = MergeTreeResult(exit_code=1, output=output)
        hotspots = resolve_merge_tree_hotspots(result)
        self.assertEqual(len(hotspots), 1)
        self.assertEqual(hotspots[0].path, "src/conflict.py")

    def test_merge_tree_fatal_exit_raises(self) -> None:
        result = MergeTreeResult(exit_code=2, output="fatal output")
        with self.assertRaises(ChangelogAnalysisError):
            _ = resolve_merge_tree_hotspots(result)

    def test_parse_diff_stats_handles_renames_and_binary(self) -> None:
        numstat = "\n".join(
            [
                "5\t3\tsrc/feature.py",
                "-\t-\tbinary/blob.dat",
                "7\t2\told/name.py => new/name.py",
            ]
        )
        parsed_numstat = parse_numstat_output(numstat)
        self.assertEqual(parsed_numstat["src/feature.py"], (5, 3))
        self.assertEqual(parsed_numstat["binary/blob.dat"], (0, 0))
        self.assertEqual(parsed_numstat["old/name.py => new/name.py"], (7, 2))

        name_status = "\n".join(
            [
                "M\tsrc/feature.py",
                "R100\told/name.py\tnew/name.py",
                "A\tbinary/blob.dat",
            ]
        )
        parsed_name_status = parse_name_status_output(name_status)
        self.assertEqual(parsed_name_status["src/feature.py"], "M")
        self.assertEqual(parsed_name_status["new/name.py"], "R")
        self.assertEqual(parsed_name_status["binary/blob.dat"], "A")

    def test_build_evidence_bundle_fetches_remotes_and_truncates_top_files(
        self,
    ) -> None:
        changed_files = [
            ChangedFileStat(path="src/a.py", added=10, removed=2, status="M"),
            ChangedFileStat(path="src/b.py", added=6, removed=1, status="A"),
            ChangedFileStat(path="src/c.py", added=4, removed=0, status="M"),
        ]
        diff_summary = DiffSummary(files_changed=3, insertions=20, deletions=3)

        with (
            patch(
                "forklift.changelog_analysis.ensure_supported_git_version",
                return_value=(2, 40, 1),
            ),
            patch(
                "forklift.changelog_analysis.ensure_required_remotes",
                return_value={"origin": object(), "upstream": object()},
            ) as remotes_mock,
            patch(
                "forklift.changelog_analysis.fetch_remotes", return_value=[]
            ) as fetch_mock,
            patch(
                "forklift.changelog_analysis.resolve_analysis_refs",
                return_value=("main", "upstream/main"),
            ),
            patch(
                "forklift.changelog_analysis.compute_merge_base", return_value="abc123"
            ),
            patch(
                "forklift.changelog_analysis.run_merge_tree",
                return_value=MergeTreeResult(exit_code=0, output="treeoid"),
            ),
            patch(
                "forklift.changelog_analysis.resolve_merge_tree_hotspots",
                return_value=[],
            ),
            patch(
                "forklift.changelog_analysis.collect_supporting_diff_stats",
                return_value=(diff_summary, changed_files),
            ),
        ):
            evidence = build_evidence_bundle(Path("."), "main", max_changed_files=2)

        remotes_mock.assert_called_once()
        fetch_mock.assert_called_once()
        self.assertEqual(evidence.base_sha, "abc123")
        self.assertEqual(
            [item.path for item in evidence.top_changed_files], ["src/a.py", "src/b.py"]
        )


class ChangelogCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _dummy_env(self) -> OpenCodeEnv:
        return OpenCodeEnv(
            api_key="api",
            model="openai:gpt-5-mini",
            variant="default",
            agent="worker",
            server_password="pw",
            server_port=4096,
        )

    def _sample_evidence(self) -> EvidenceBundle:
        return EvidenceBundle(
            base_sha="1234567890abcdef1234567890abcdef12345678",
            main_branch="main",
            upstream_ref="upstream/main",
            conflicts=[ConflictHotspot(path="src/conflict.py", conflict_count=2)],
            diff_summary=DiffSummary(files_changed=2, insertions=9, deletions=4),
            top_changed_files=[
                ChangedFileStat(path="src/conflict.py", added=7, removed=3, status="M"),
                ChangedFileStat(path="src/new.py", added=2, removed=1, status="A"),
            ],
            important_notes=["Conflict hotspots are deterministic predictions."],
        )

    async def test_successful_flow_builds_evidence_calls_llm_and_renders_sections(
        self,
    ) -> None:
        command = Changelog(main_branch="main")
        captured: dict[str, str] = {}

        def _capture_markdown(markdown: str) -> str:
            return captured.setdefault("markdown", markdown)

        with (
            patch.object(
                command, "_prepare_opencode_env", return_value=self._dummy_env()
            ),
            patch(
                "forklift.changelog.build_evidence_bundle",
                return_value=self._sample_evidence(),
            ) as evidence_mock,
            patch(
                "forklift.changelog.generate_changelog_narrative",
                return_value=(
                    "## Summary\n"
                    "Main branch diverges from upstream.\n\n"
                    "## Notable Change Themes\n"
                    "- Refactors in src/.\n\n"
                    "## Risk and Review Notes\n"
                    "- Check parser edge cases."
                ),
            ) as llm_mock,
            patch(
                "forklift.changelog.render_changelog_terminal",
                side_effect=_capture_markdown,
            ) as render_mock,
        ):
            await command.run()

        evidence_mock.assert_called_once()
        llm_mock.assert_called_once()
        render_mock.assert_called_once()
        output = captured["markdown"]
        self.assertIn("# Forklift Changelog", output)
        self.assertIn("## Branch Context", output)
        self.assertIn("## Summary", output)
        self.assertIn("## Predicted Conflict Hotspots", output)
        self.assertIn("## Deterministic Supporting Metrics", output)

    async def test_llm_failure_exits_nonzero_without_fallback_render(self) -> None:
        command = Changelog(main_branch="main")

        with (
            patch.object(
                command, "_prepare_opencode_env", return_value=self._dummy_env()
            ),
            patch(
                "forklift.changelog.build_evidence_bundle",
                return_value=self._sample_evidence(),
            ),
            patch(
                "forklift.changelog.generate_changelog_narrative",
                side_effect=ChangelogLlmError("model auth failed"),
            ),
            patch("forklift.changelog.render_changelog_terminal") as render_mock,
        ):
            with self.assertRaises(SystemExit) as ctx:
                await command.run()

        self.assertNotEqual(ctx.exception.code, 0)
        render_mock.assert_not_called()

    def test_changelog_subcommand_does_not_invoke_orchestration_helpers(self) -> None:
        command = Forklift.parse(["changelog"])

        with (
            patch.object(
                Changelog, "run", new=AsyncMock(return_value=None)
            ) as changelog_run,
            patch.object(
                Forklift,
                "run",
                new=AsyncMock(
                    side_effect=AssertionError(
                        "Forklift.run should not execute for changelog subcommand"
                    )
                ),
            ),
            patch(
                "forklift.cli.RunDirectoryManager.prepare",
                side_effect=AssertionError("prepare should not run"),
            ),
            patch(
                "forklift.cli.ContainerRunner.run",
                side_effect=AssertionError("container should not run"),
            ),
            patch(
                "forklift.cli.post_container_results",
                side_effect=AssertionError("post-run should not run"),
            ),
            patch(
                "forklift.cli.rewrite_and_publish_local",
                side_effect=AssertionError("publish helper should not run"),
            ),
        ):
            error = command.start()

        self.assertIsNone(error)
        self.assertEqual(changelog_run.await_count, 1)


if __name__ == "__main__":
    _ = unittest.main()
