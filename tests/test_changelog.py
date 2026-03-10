from __future__ import annotations

# pyright: reportAny=false

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from forklift.changelog import Changelog, load_changelog_exclude_patterns
from forklift.changelog_analysis import (
    ChangelogAnalysisError,
    DEFAULT_SIDE_COMMIT_SAMPLE_CAP,
    DEFAULT_SIDE_HUNK_HEADER_CAP,
    MergeTreeResult,
    build_conflict_side_comparisons,
    build_evidence_bundle,
    collect_conflict_side_evidence,
    compute_side_local_churn,
    extract_hunk_headers,
    filter_changed_file_stats,
    is_path_excluded,
    parse_oneline_commit_samples,
    parse_merge_tree_conflict_hotspots,
    parse_name_status_output,
    parse_numstat_output,
    resolve_merge_tree_hotspots,
)
from forklift.changelog_llm import (
    ChangelogLlmError,
    NARRATIVE_SYSTEM_PROMPT,
    generate_changelog_narrative,
)
from forklift.changelog_models import (
    ChangedFileStat,
    CommitSample,
    ConflictHotspot,
    ConflictSideComparison,
    ConflictSideEvidence,
    DiffSummary,
    EvidenceBundle,
    TruncationMetadata,
)
from forklift.cli import Forklift
from forklift.opencode_env import OpenCodeEnv
from forklift.changelog_renderer import render_changelog_markdown, render_changelog_terminal


def lines(*rows: str) -> str:
    return "\n".join(rows)


class ChangelogCliParsingTests(unittest.TestCase):
    def test_forklift_parse_routes_changelog_subcommand(self) -> None:
        command = Forklift.parse(["changelog"])
        self.assertIsInstance(command.subcommand, Changelog)


class ChangelogForkMetadataTests(unittest.TestCase):
    def test_load_exclusions_reads_changelog_front_matter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _ = (repo / "FORK.md").write_text(
                lines(
                    "---",
                    "setup: echo ok",
                    "changelog:",
                    "  exclude:",
                    "    - data/big.json",
                    "    - !data/keep.json",
                    "---",
                    "## Mission",
                    "Keep behavior stable.",
                )
                + "\n",
                encoding="utf-8",
            )

            excludes = load_changelog_exclude_patterns(repo)

        self.assertEqual(excludes, ["data/big.json", "!data/keep.json"])

    def test_load_exclusions_rejects_invalid_changelog_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _ = (repo / "FORK.md").write_text(
                lines(
                    "---",
                    "changelog: []",
                    "---",
                    "## Mission",
                    "Keep behavior stable.",
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ChangelogAnalysisError):
                _ = load_changelog_exclude_patterns(repo)


class ChangelogModelTests(unittest.TestCase):
    def test_dataclass_defaults_and_field_types(self) -> None:
        hotspot = ConflictHotspot(path="src/app.py")
        summary = DiffSummary()
        file_stat = ChangedFileStat(path="src/app.py")
        truncation = TruncationMetadata(shown=2, total=5, cap=2)
        commit_sample = CommitSample(short_sha="abc1234", subject="Refactor parser")
        side = ConflictSideEvidence(
            commit_samples=[commit_sample],
            insertions=4,
            deletions=1,
            hunk_headers=["@@ -10,2 +10,3 @@"],
            commit_samples_truncation=truncation,
        )
        comparison = ConflictSideComparison(
            path="src/app.py",
            conflict_count=3,
            fork_side=side,
            upstream_side=ConflictSideEvidence(),
        )
        evidence = EvidenceBundle(
            base_sha="abc123",
            main_branch="main",
            upstream_ref="upstream/main",
            conflict_side_comparisons=[comparison],
        )

        self.assertEqual(hotspot.conflict_count, 1)
        self.assertEqual(summary.files_changed, 0)
        self.assertEqual(summary.insertions, 0)
        self.assertEqual(summary.deletions, 0)
        self.assertEqual(file_stat.status, "M")
        self.assertEqual(file_stat.added, 0)
        self.assertEqual(file_stat.removed, 0)
        self.assertEqual(commit_sample.short_sha, "abc1234")
        self.assertEqual(truncation.cap, 2)
        self.assertEqual(side.insertions, 4)
        self.assertEqual(side.deletions, 1)

        self.assertIsInstance(evidence.conflicts, list)
        self.assertIsInstance(evidence.baseline_diff_summary, DiffSummary)
        self.assertIsInstance(evidence.filtered_diff_summary, DiffSummary)
        self.assertIsInstance(evidence.active_exclusion_rules, list)
        self.assertIsInstance(evidence.excluded_file_count, int)
        self.assertIsInstance(evidence.diff_summary, DiffSummary)
        self.assertIsInstance(evidence.top_changed_files, list)
        self.assertIsInstance(evidence.conflict_side_comparisons, list)
        self.assertIsInstance(evidence.important_notes, list)

        serialized = asdict(evidence)
        self.assertIn("conflict_side_comparisons", serialized)
        self.assertEqual(
            serialized["conflict_side_comparisons"][0]["fork_side"]["commit_samples"][0][
                "short_sha"
            ],
            "abc1234",
        )


class ChangelogLlmTests(unittest.TestCase):
    def _sample_evidence(self) -> EvidenceBundle:
        return EvidenceBundle(
            base_sha="1234567890abcdef1234567890abcdef12345678",
            main_branch="main",
            upstream_ref="upstream/main",
            diff_summary=DiffSummary(files_changed=0, insertions=0, deletions=0),
        )

    def test_generate_narrative_normalizes_slash_model_and_sets_google_provider_keys(
        self,
    ) -> None:
        env = OpenCodeEnv(
            api_key="opencode",
            model="google/gemini-3-flash-preview",
            variant="default",
            agent="worker",
            server_password="pw",
            server_port=4096,
            google_generative_ai_api_key="google-key",
        )
        captured: dict[str, str] = {}

        class FakeAgent:
            def __init__(self, model: str, *, system_prompt: str) -> None:
                captured["model"] = model
                captured["system_prompt"] = system_prompt
                captured["google_api_key"] = os.environ.get("GOOGLE_API_KEY", "")
                captured["gemini_api_key"] = os.environ.get("GEMINI_API_KEY", "")

            async def run(self, prompt: str) -> SimpleNamespace:
                captured["prompt"] = prompt
                return SimpleNamespace(output="## Summary\nNormalized model name works")

        with patch.dict(os.environ, {}, clear=True):
            with patch("forklift.changelog_llm.Agent", FakeAgent):
                output = asyncio.run(
                    generate_changelog_narrative(self._sample_evidence(), env)
                )

            self.assertEqual(captured["model"], "google-gla:gemini-3-flash-preview")
            self.assertEqual(captured["google_api_key"], "google-key")
            self.assertEqual(captured["gemini_api_key"], "google-key")
            self.assertNotIn("GOOGLE_API_KEY", os.environ)
            self.assertNotIn("GEMINI_API_KEY", os.environ)
            self.assertIn("## Key Change Arcs", captured["system_prompt"])
            self.assertIn("## Conflict Pair Evaluations", captured["system_prompt"])
            self.assertIn("Fork-side intent", captured["system_prompt"])
            self.assertIn("insufficient evidence", captured["system_prompt"])
            self.assertIn("Define the arc in plain language", captured["system_prompt"])
            self.assertIn("Use only this evidence.", captured["prompt"])
            self.assertIn(
                "Conflict side comparisons and any truncation metadata are authoritative",
                captured["prompt"],
            )
            self.assertIn("Evidence JSON", captured["prompt"])

        self.assertEqual(output, "## Summary\nNormalized model name works")

    def test_narrative_contract_requires_conflict_pair_evaluations_heading(self) -> None:
        self.assertIn("## Conflict Pair Evaluations", NARRATIVE_SYSTEM_PROMPT)

    def test_narrative_contract_requires_insufficient_evidence_wording(self) -> None:
        self.assertIn("insufficient evidence", NARRATIVE_SYSTEM_PROMPT)


class ChangelogRendererTests(unittest.TestCase):
    def test_render_terminal_caps_width_at_110_columns(self) -> None:
        console = Mock()
        console.width = 180

        render_changelog_terminal("## Summary\nTest", console=console)

        args, kwargs = console.print.call_args
        self.assertEqual(kwargs["width"], 110)
        self.assertEqual(type(args[0]).__name__, "Markdown")

    def test_render_terminal_passes_cap_width_even_when_console_is_narrow(self) -> None:
        console = Mock()
        console.width = 88

        render_changelog_terminal("## Summary\nTest", console=console)

        _, kwargs = console.print.call_args
        self.assertEqual(kwargs["width"], 110)

    def test_render_markdown_includes_metric_comparison_and_exclusion_summary(self) -> None:
        evidence = EvidenceBundle(
            base_sha="1234567890abcdef1234567890abcdef12345678",
            main_branch="main",
            upstream_ref="upstream/main",
            conflicts=[ConflictHotspot(path="src/conflict.py", conflict_count=2)],
            baseline_diff_summary=DiffSummary(files_changed=204, insertions=16628, deletions=4526),
            filtered_diff_summary=DiffSummary(files_changed=57, insertions=1140, deletions=390),
            active_exclusion_rules=["data/big-snapshot.json", "!data/keep.json"],
            excluded_file_count=147,
            diff_summary=DiffSummary(files_changed=57, insertions=1140, deletions=390),
            top_changed_files=[
                ChangedFileStat(path="src/conflict.py", added=7, removed=3, status="M")
            ],
            conflict_side_comparisons=[
                ConflictSideComparison(
                    path="src/conflict.py",
                    conflict_count=2,
                    fork_side=ConflictSideEvidence(
                        commit_samples=[
                            CommitSample(short_sha="abc1234", subject="Adjust conflict flow")
                        ],
                        insertions=7,
                        deletions=3,
                        hunk_headers=["@@ -5,2 +5,4 @@"],
                        commit_samples_truncation=TruncationMetadata(
                            shown=1,
                            total=3,
                            cap=1,
                        ),
                    ),
                    upstream_side=ConflictSideEvidence(
                        commit_samples=[],
                        insertions=2,
                        deletions=1,
                        hunk_headers=[],
                    ),
                )
            ],
        )

        markdown = render_changelog_markdown(evidence, "## Summary\nNarrative")

        self.assertIn("| Metric | All Files | Excluding Patterns | Delta |", markdown)
        self.assertIn("| Files changed | 204 | 57 | -147 |", markdown)
        self.assertIn("### Exclusion Rules", markdown)
        self.assertIn("- `data/big-snapshot.json`", markdown)
        self.assertIn("- Matched files in baseline diff: 147", markdown)
        self.assertIn("## Conflict Side Comparisons", markdown)
        self.assertIn("### `src/conflict.py` (conflict count: 2)", markdown)
        self.assertIn("- Commit samples truncation: 1/3 (cap 1)", markdown)
        self.assertIn("- Warning: additional evidence exists beyond configured limits.", markdown)

    def test_render_markdown_omits_conflict_side_section_when_comparisons_absent(self) -> None:
        evidence = EvidenceBundle(
            base_sha="1234567890abcdef1234567890abcdef12345678",
            main_branch="main",
            upstream_ref="upstream/main",
            conflicts=[],
        )

        markdown = render_changelog_markdown(evidence, "## Summary\nNarrative")

        self.assertNotIn("## Conflict Side Comparisons", markdown)


class ChangelogAnalysisTests(unittest.TestCase):
    def test_parse_merge_tree_conflicts_no_conflicts(self) -> None:
        output = "f00ba47f00ba47f00ba47f00ba47f00ba47f00ba"
        self.assertEqual(parse_merge_tree_conflict_hotspots(output), [])

    def test_parse_merge_tree_conflicts_counts_multiple_blocks_for_one_file(
        self,
    ) -> None:
        output = lines(
            "beadbeadbeadbeadbeadbeadbeadbeadbeadbead",
            "100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 src/conflict.py",
            "100644 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 src/conflict.py",
            "100644 cccccccccccccccccccccccccccccccccccccccc 3 src/conflict.py",
            "100644 dddddddddddddddddddddddddddddddddddddddd 1 src/conflict.py",
            "100644 eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee 2 src/conflict.py",
            "100644 ffffffffffffffffffffffffffffffffffffffff 3 src/conflict.py",
        )

        hotspots = parse_merge_tree_conflict_hotspots(output)
        self.assertEqual(len(hotspots), 1)
        self.assertEqual(hotspots[0].path, "src/conflict.py")
        self.assertEqual(hotspots[0].conflict_count, 2)

    def test_parse_merge_tree_conflicts_multiple_files(self) -> None:
        output = lines(
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
        output = lines(
            "treeoid",
            "100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 src/conflict.py",
            "100644 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 src/conflict.py",
            "100644 cccccccccccccccccccccccccccccccccccccccc 3 src/conflict.py",
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
        numstat = lines(
            "5\t3\tsrc/feature.py",
            "-\t-\tbinary/blob.dat",
            "7\t2\told/name.py => new/name.py",
        )
        parsed_numstat = parse_numstat_output(numstat)
        self.assertEqual(parsed_numstat["src/feature.py"], (5, 3))
        self.assertEqual(parsed_numstat["binary/blob.dat"], (0, 0))
        self.assertEqual(parsed_numstat["new/name.py"], (7, 2))

        name_status = lines(
            "M\tsrc/feature.py",
            "R100\told/name.py\tnew/name.py",
            "A\tbinary/blob.dat",
        )
        parsed_name_status = parse_name_status_output(name_status)
        self.assertEqual(parsed_name_status["src/feature.py"], "M")
        self.assertEqual(parsed_name_status["new/name.py"], "R")
        self.assertEqual(parsed_name_status["binary/blob.dat"], "A")

    def test_helper_parsers_extract_commit_samples_hunk_headers_and_churn(self) -> None:
        commit_lines = lines("abc1234 Add parser guard", "def5678")
        samples = parse_oneline_commit_samples(commit_lines)
        self.assertEqual(
            samples,
            [
                CommitSample(short_sha="abc1234", subject="Add parser guard"),
                CommitSample(short_sha="def5678", subject=""),
            ],
        )

        diff_text = lines(
            "diff --git a/src/app.py b/src/app.py",
            "@@ -1,2 +1,3 @@ def run():",
            "+print('x')",
            "@@ -10 +11 @@ class X:",
        )
        self.assertEqual(
            extract_hunk_headers(diff_text),
            ["@@ -1,2 +1,3 @@ def run():", "@@ -10 +11 @@ class X:"],
        )

        churn = compute_side_local_churn(lines("5\t2\tsrc/app.py", "-\t-\tbinary/blob.dat"))
        self.assertEqual(churn, (5, 2))

    def test_collect_conflict_side_evidence_applies_caps_and_sets_truncation(self) -> None:
        self.assertGreater(DEFAULT_SIDE_COMMIT_SAMPLE_CAP, 0)
        self.assertGreater(DEFAULT_SIDE_HUNK_HEADER_CAP, 0)

        with patch(
            "forklift.changelog_analysis.run_git",
            side_effect=[
                lines("aaa1111 first", "bbb2222 second", "ccc3333 third"),
                lines(
                    "diff --git a/src/app.py b/src/app.py",
                    "@@ -1 +1 @@",
                    "@@ -10,2 +12,3 @@ def parse():",
                ),
                lines("9\t4\tsrc/app.py"),
            ],
        ):
            side = collect_conflict_side_evidence(
                Path("."),
                base_sha="base",
                side_ref="main",
                conflict_path="src/app.py",
                commit_sample_cap=1,
                hunk_header_cap=1,
            )

        self.assertEqual(len(side.commit_samples), 1)
        self.assertEqual(side.commit_samples[0].short_sha, "aaa1111")
        self.assertEqual(side.hunk_headers, ["@@ -1 +1 @@"])
        self.assertEqual(side.insertions, 9)
        self.assertEqual(side.deletions, 4)
        self.assertEqual(side.commit_samples_truncation, TruncationMetadata(1, 3, 1))
        self.assertEqual(side.hunk_headers_truncation, TruncationMetadata(1, 2, 1))

    def test_build_conflict_side_comparisons_preserves_order_and_sparse_sides(self) -> None:
        hotspots = [
            ConflictHotspot(path="b.py", conflict_count=5),
            ConflictHotspot(path="a.py", conflict_count=5),
            ConflictHotspot(path="c.py", conflict_count=2),
        ]

        def _fake_side(
            _repo_path: Path,
            *,
            conflict_path: str,
            side_ref: str,
            **_: object,
        ) -> ConflictSideEvidence:
            if side_ref == "upstream/main" and conflict_path == "c.py":
                return ConflictSideEvidence()
            return ConflictSideEvidence(
                commit_samples=[CommitSample(short_sha=f"{conflict_path}-1", subject=side_ref)],
                insertions=1,
                deletions=0,
                hunk_headers=["@@ -1 +1 @@"],
            )

        with patch(
            "forklift.changelog_analysis.collect_conflict_side_evidence",
            side_effect=_fake_side,
        ):
            comparisons = build_conflict_side_comparisons(
                Path("."),
                base_sha="base",
                main_branch="main",
                upstream_ref="upstream/main",
                hotspots=hotspots,
            )

        self.assertEqual([item.path for item in comparisons], ["a.py", "b.py", "c.py"])
        self.assertEqual(comparisons[2].upstream_side.commit_samples, [])

    def test_exclusion_matching_supports_negation_and_last_match_wins(self) -> None:
        rules = ["generated/**", "!generated/keep.json"]
        self.assertTrue(is_path_excluded("generated/skip.json", rules))
        self.assertFalse(is_path_excluded("generated/keep.json", rules))

    def test_filter_changed_files_counts_excluded_rows(self) -> None:
        changed = [
            ChangedFileStat(path="generated/skip.json", added=10, removed=0, status="M"),
            ChangedFileStat(path="generated/keep.json", added=3, removed=1, status="M"),
            ChangedFileStat(path="src/app.py", added=1, removed=1, status="M"),
        ]

        filtered, excluded_count = filter_changed_file_stats(
            changed,
            ["generated/**", "!generated/keep.json"],
        )

        self.assertEqual(excluded_count, 1)
        self.assertEqual([item.path for item in filtered], ["generated/keep.json", "src/app.py"])

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
        self.assertEqual(evidence.baseline_diff_summary, diff_summary)
        self.assertEqual(evidence.filtered_diff_summary, diff_summary)
        self.assertEqual(evidence.excluded_file_count, 0)
        self.assertEqual(evidence.conflict_side_comparisons, [])

    def test_build_evidence_bundle_applies_exclusion_rules_to_metrics_and_hotspots(self) -> None:
        changed_files = [
            ChangedFileStat(path="data/big.json", added=100, removed=20, status="M"),
            ChangedFileStat(path="src/app.py", added=5, removed=1, status="M"),
        ]
        baseline_summary = DiffSummary(files_changed=2, insertions=105, deletions=21)
        side_comparison = ConflictSideComparison(
            path="src/app.py",
            conflict_count=1,
            fork_side=ConflictSideEvidence(),
            upstream_side=ConflictSideEvidence(),
        )

        with (
            patch(
                "forklift.changelog_analysis.ensure_supported_git_version",
                return_value=(2, 40, 1),
            ),
            patch(
                "forklift.changelog_analysis.ensure_required_remotes",
                return_value={"origin": object(), "upstream": object()},
            ),
            patch("forklift.changelog_analysis.fetch_remotes", return_value=[]),
            patch(
                "forklift.changelog_analysis.resolve_analysis_refs",
                return_value=("main", "upstream/main"),
            ),
            patch("forklift.changelog_analysis.compute_merge_base", return_value="abc123"),
            patch(
                "forklift.changelog_analysis.run_merge_tree",
                return_value=MergeTreeResult(exit_code=1, output="treeoid"),
            ),
            patch(
                "forklift.changelog_analysis.resolve_merge_tree_hotspots",
                return_value=[
                    ConflictHotspot(path="data/big.json", conflict_count=3),
                    ConflictHotspot(path="src/app.py", conflict_count=1),
                ],
            ),
            patch(
                "forklift.changelog_analysis.collect_supporting_diff_stats",
                return_value=(baseline_summary, changed_files),
            ),
            patch(
                "forklift.changelog_analysis.build_conflict_side_comparisons",
                return_value=[side_comparison],
            ) as comparisons_mock,
        ):
            evidence = build_evidence_bundle(
                Path("."),
                "main",
                exclusion_patterns=["data/**"],
            )

        self.assertEqual(evidence.baseline_diff_summary.files_changed, 2)
        self.assertEqual(evidence.filtered_diff_summary.files_changed, 1)
        self.assertEqual(evidence.excluded_file_count, 1)
        self.assertEqual([item.path for item in evidence.top_changed_files], ["src/app.py"])
        self.assertEqual([item.path for item in evidence.conflicts], ["src/app.py"])
        self.assertEqual(
            [item.path for item in evidence.conflict_side_comparisons],
            ["src/app.py"],
        )

        kwargs = comparisons_mock.call_args.kwargs
        self.assertEqual([item.path for item in kwargs["hotspots"]], ["src/app.py"])


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

    def _patch_env(self, command: Changelog):
        return patch.object(
            command, "_prepare_opencode_env", return_value=self._dummy_env()
        )

    def _assert_markdown_sections(self, output: str) -> None:
        self.assertIn("# Forklift Changelog", output)
        self.assertIn("## Branch Context", output)
        self.assertIn("## Summary", output)
        self.assertIn("## Predicted Conflict Hotspots", output)
        self.assertIn("## Deterministic Supporting Metrics", output)

    async def test_successful_flow_builds_evidence_calls_llm_and_renders_sections(
        self,
    ) -> None:
        command = Changelog(main_branch="main")
        captured: dict[str, str] = {}

        def _capture_markdown(markdown: str) -> str:
            return captured.setdefault("markdown", markdown)

        with (
            self._patch_env(command),
            patch(
                "forklift.changelog.build_evidence_bundle",
                return_value=self._sample_evidence(),
            ) as evidence_mock,
            patch(
                "forklift.changelog.generate_changelog_narrative",
                new=AsyncMock(
                    return_value=(
                        "## Summary\n"
                        "Main branch diverges from upstream.\n\n"
                        "## Key Change Arcs\n"
                        "- Refactors in src/.\n\n"
                        "## Risk and Review Notes\n"
                        "- Check parser edge cases."
                    )
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
        self._assert_markdown_sections(output)

    async def test_llm_failure_exits_nonzero_without_fallback_render(self) -> None:
        command = Changelog(main_branch="main")

        with (
            self._patch_env(command),
            patch(
                "forklift.changelog.build_evidence_bundle",
                return_value=self._sample_evidence(),
            ),
            patch(
                "forklift.changelog.generate_changelog_narrative",
                new=AsyncMock(side_effect=ChangelogLlmError("model auth failed")),
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
