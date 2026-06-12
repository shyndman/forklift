from __future__ import annotations

from decimal import Decimal
import json
from io import StringIO
import tempfile
import unittest
from pathlib import Path

from rich.console import Console

from forklift.post_run_metrics import (
    ToolCallTotal,
    UsageTotals,
    UsageSummary,
    parse_usage_summary,
    render_completion_report,
    render_usage_summary,
)


class ParseUsageSummaryTests(unittest.TestCase):
    def _write_log(self, root: Path, lines: list[str]) -> Path:
        log_path = root / "opencode-client.log"
        _ = log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path

    def _write_conflicting_commits(self, root: Path, payload: str) -> Path:
        harness_state = root / "harness-state"
        harness_state.mkdir(exist_ok=True)
        # Conflicting-commit count now derives from rebase-report.json resolutions.
        report_path = harness_state / "rebase-report.json"
        _ = report_path.write_text(
            '{"outcome":"completed","resolutions":'
            + payload
            + ',"skips":[],"stuck":null}',
            encoding="utf-8",
        )
        return harness_state

    def test_parses_cost_totals_and_final_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = self._write_log(
                root,
                [
                    "2026-03-01T00:00:00Z harness: boot",
                    json.dumps(
                        {
                            "type": "step_start",
                            "timestamp": 1_000,
                            "part": {"cost": 999},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "tool_use",
                            "timestamp": 2_000,
                            "part": {
                                "tool": "bash",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "step_finish",
                            "timestamp": 3_000,
                            "part": {
                                "cost": 0.125,
                                "tokens": {
                                    "input": 40,
                                    "output": 10,
                                    "reasoning": 3,
                                    "cache": {"read": 2},
                                    "total": 53,
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "tool_use",
                            "timestamp": 6_000,
                            "part": {
                                "tool": "read",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "tool_use",
                            "timestamp": 7_000,
                            "part": {
                                "tool": "bash",
                            },
                        }
                    ),
                    "{bad-json",
                    json.dumps(
                        {
                            "type": "step_finish",
                            "timestamp": 8_000,
                            "part": {
                                "cost": 0.375,
                                "tokens": {
                                    "input": 100,
                                    "output": 50,
                                    "reasoning": 25,
                                    "cache": {"read": 6},
                                    "total": 181,
                                },
                            },
                        }
                    ),
                ],
            )
            harness_state = self._write_conflicting_commits(
                root,
                json.dumps([{"sha": "abc1234", "subject": "Resolve drift"}]) + "\n",
            )

            summary = parse_usage_summary(log_path, harness_state=harness_state)

        self.assertTrue(summary.available)
        assert summary.totals is not None
        self.assertEqual(summary.totals.input_tokens, 100)
        self.assertEqual(summary.totals.output_tokens, 50)
        self.assertEqual(summary.totals.reasoning_tokens, 25)
        self.assertEqual(summary.totals.cache_read_tokens, 6)
        self.assertEqual(summary.totals.total_tokens, 181)
        assert summary.totals.total_cost is not None
        self.assertEqual(summary.totals.total_cost, 0.5)
        self.assertEqual(summary.totals.wall_clock_ms, 7_000)
        self.assertEqual(summary.totals.tool_calls, 3)
        self.assertEqual(summary.totals.conflicting_commits, 1)
        self.assertEqual(
            summary.totals.tool_breakdown,
            (
                ToolCallTotal(tool="bash", calls=2),
                ToolCallTotal(tool="read", calls=1),
            ),
        )

    def test_defaults_missing_component_fields_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = self._write_log(
                root,
                [
                    json.dumps(
                        {
                            "type": "step_finish",
                            "part": {
                                "cost": 0.2,
                                "tokens": {
                                    "total": 99,
                                },
                            },
                        }
                    )
                ],
            )
            harness_state = self._write_conflicting_commits(root, "[]\n")

            summary = parse_usage_summary(log_path, harness_state=harness_state)

        self.assertTrue(summary.available)
        assert summary.totals is not None
        self.assertEqual(summary.totals.input_tokens, 0)
        self.assertEqual(summary.totals.output_tokens, 0)
        self.assertEqual(summary.totals.reasoning_tokens, 0)
        self.assertEqual(summary.totals.cache_read_tokens, 0)
        self.assertEqual(summary.totals.total_tokens, 99)
        assert summary.totals.total_cost is not None
        self.assertEqual(summary.totals.total_cost, 0.2)
        self.assertEqual(summary.totals.wall_clock_ms, 0)
        self.assertEqual(summary.totals.tool_calls, 0)
        self.assertEqual(summary.totals.conflicting_commits, 0)
        self.assertEqual(summary.totals.tool_breakdown, ())

    def test_returns_unavailable_when_no_valid_usage_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = self._write_log(
                root,
                [
                    "not-json",
                    json.dumps({"type": "message", "part": {"cost": 0.2}}),
                    json.dumps({"type": "step_finish", "part": {"cost": "0.5"}}),
                ],
            )
            harness_state = self._write_conflicting_commits(root, "[]\n")

            summary = parse_usage_summary(log_path, harness_state=harness_state)

        self.assertFalse(summary.available)
        self.assertEqual(summary.reason_unavailable, "no usage events found")

    def test_falls_back_to_zero_conflicting_commits_when_artifact_missing_or_malformed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = self._write_log(
                root,
                [
                    json.dumps(
                        {
                            "type": "step_finish",
                            "part": {
                                "cost": 0.2,
                                "tokens": {
                                    "total": 99,
                                },
                            },
                        }
                    )
                ],
            )
            missing_harness_state = root / "missing-harness-state"

            summary = parse_usage_summary(log_path, harness_state=missing_harness_state)

            self.assertTrue(summary.available)
            assert summary.totals is not None
            self.assertEqual(summary.totals.conflicting_commits, 0)

            harness_state = self._write_conflicting_commits(root, "{bad-json\n")

            malformed_summary = parse_usage_summary(
                log_path, harness_state=harness_state
            )

        self.assertTrue(malformed_summary.available)
        assert malformed_summary.totals is not None
        self.assertEqual(malformed_summary.totals.conflicting_commits, 0)

    def test_conflicting_commit_count_includes_resolutions_and_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = self._write_log(
                root,
                [
                    json.dumps(
                        {
                            "type": "step_finish",
                            "part": {"cost": 0.2, "tokens": {"total": 99}},
                        }
                    )
                ],
            )
            harness_state = root / "harness-state"
            harness_state.mkdir()
            _ = (harness_state / "rebase-report.json").write_text(
                json.dumps(
                    {
                        "outcome": "completed",
                        "resolutions": [
                            {"sha": "aaa1111", "subject": "Resolve A", "note": "x"},
                            {"sha": "bbb2222", "subject": "Resolve B", "note": "y"},
                        ],
                        "skips": [
                            {"sha": "ccc3333", "subject": "Drop C", "note": "z"},
                        ],
                        "stuck": None,
                    }
                ),
                encoding="utf-8",
            )

            summary = parse_usage_summary(log_path, harness_state=harness_state)

        assert summary.totals is not None
        # 2 resolved + 1 skipped commit, both of which paused on a conflict.
        self.assertEqual(summary.totals.conflicting_commits, 3)


class RenderUsageSummaryTests(unittest.TestCase):
    def _console(self, buffer: StringIO) -> Console:
        return Console(file=buffer, force_terminal=False, color_system=None, width=110)

    def test_renders_centered_rounded_table_with_expected_rows(self) -> None:
        rendered = StringIO()
        render_usage_summary(
            "success",
            UsageSummary.from_totals(
                UsageTotals(
                    input_tokens=1,
                    output_tokens=22,
                    reasoning_tokens=333,
                    cache_read_tokens=4,
                    total_tokens=360,
                    total_cost=0.12,
                    wall_clock_ms=12_345,
                    tool_calls=3,
                    conflicting_commits=2,
                    tool_breakdown=(
                        ToolCallTotal(tool="read", calls=2),
                        ToolCallTotal(tool="write", calls=1),
                    ),
                )
            ),
            console=self._console(rendered),
        )
        output = rendered.getvalue()

        self.assertIn("Metric", output)
        self.assertIn("Value", output)
        self.assertIn("╭", output)
        self.assertIn("╰", output)

        expected_rows = [
            "Input",
            "Output",
            "Reasoning",
            "Cache read",
            "Total tokens",
            "Wall clock",
            "Tool calls",
            "↳ read",
            "↳ write",
            "Conflicting commits",
            "Total cost",
        ]
        positions = [output.index(label) for label in expected_rows]
        self.assertEqual(positions, sorted(positions))

        top_border = next(line for line in output.splitlines() if "╭" in line)
        self.assertGreater(len(top_border) - len(top_border.lstrip(" ")), 5)
        self.assertGreaterEqual(len(top_border.strip()), 80)

    def test_formats_numbers_and_unavailable_path(self) -> None:
        totals_summary = UsageSummary.from_totals(
            UsageTotals(
                input_tokens=1234,
                output_tokens=56,
                reasoning_tokens=7,
                cache_read_tokens=8,
                total_tokens=1305,
                total_cost=0.6562,
                wall_clock_ms=67_890,
                tool_calls=14,
                conflicting_commits=3,
                tool_breakdown=(ToolCallTotal(tool="bash", calls=14),),
            )
        )
        rendered = StringIO()
        render_usage_summary("success", totals_summary, console=self._console(rendered))
        output = rendered.getvalue()

        self.assertIn("Run complete: success", output)
        self.assertIn("Grand total", output)
        self.assertIn("1,234", output)
        self.assertIn("1,305", output)
        self.assertIn("$0.6562", output)
        self.assertIn("01:07.890", output)
        self.assertIn("14", output)
        self.assertIn("3", output)
        self.assertIn("↳ bash", output)
        self.assertIn("Metric", output)
        self.assertIn("Value", output)

    def test_formats_fractional_cent_cost_without_rounding_away_precision(self) -> None:
        rendered = StringIO()
        render_usage_summary(
            "success",
            UsageSummary.from_totals(
                UsageTotals(
                    input_tokens=9,
                    output_tokens=61,
                    reasoning_tokens=60,
                    cache_read_tokens=0,
                    total_tokens=70,
                    total_cost=Decimal("0.0001875"),
                    wall_clock_ms=321,
                    tool_calls=0,
                    conflicting_commits=0,
                    tool_breakdown=(),
                )
            ),
            console=self._console(rendered),
        )
        output = rendered.getvalue()

        self.assertIn("$0.0001875", output)

    def test_omits_total_cost_row_and_prints_notice_when_requested(self) -> None:
        rendered = StringIO()
        render_usage_summary(
            "changelog",
            UsageSummary.from_totals(
                UsageTotals(
                    input_tokens=9,
                    output_tokens=61,
                    reasoning_tokens=60,
                    cache_read_tokens=0,
                    total_tokens=70,
                    total_cost=None,
                    wall_clock_ms=321,
                    tool_calls=0,
                    conflicting_commits=0,
                    tool_breakdown=(),
                ),
                post_table_notice="Pricing information could not be shown",
            ),
            console=self._console(rendered),
        )
        output = rendered.getvalue()

        self.assertIn("Run complete: changelog", output)
        self.assertIn("Grand total", output)
        self.assertIn("Total tokens", output)
        self.assertNotIn("Total cost", output)
        self.assertIn("Pricing information could not be shown", output)

        unavailable = StringIO()
        render_usage_summary(
            "failure",
            UsageSummary.unavailable("no usage events found"),
            console=self._console(unavailable),
        )
        unavailable_output = unavailable.getvalue()
        self.assertIn("Run complete: failure", unavailable_output)
        self.assertIn("Grand total: unavailable", unavailable_output)
        self.assertIn("Reason: no usage events found", unavailable_output)


class RenderCompletionReportTests(unittest.TestCase):
    def _console(self, buffer: StringIO) -> Console:
        return Console(file=buffer, force_terminal=False, color_system=None, width=110)

    def _write_report(self, harness_state: Path, payload: dict[str, object]) -> None:
        harness_state.mkdir(parents=True, exist_ok=True)
        _ = (harness_state / "rebase-report.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_stuck_report_renders_reason_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness_state = Path(temp_dir) / "harness-state"
            self._write_report(
                harness_state,
                {
                    "outcome": "stuck",
                    "resolutions": [],
                    "skips": [],
                    "stuck": {
                        "sha": "abc1234",
                        "subject": "Adopt upstream auth",
                        "reason": "Investigate manually",
                    },
                },
            )
            rendered = StringIO()
            selected = render_completion_report(
                harness_state=harness_state, console=self._console(rendered)
            )

        assert selected is not None
        self.assertEqual(selected.name, "rebase-report.json")
        output = rendered.getvalue()
        self.assertIn("Rebase Stuck", output)
        self.assertIn("Investigate manually", output)
        self.assertIn("Resolved Conflicts", output)
        self.assertIn("Skipped Commits", output)

    def test_completed_report_lists_resolutions_and_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness_state = Path(temp_dir) / "harness-state"
            self._write_report(
                harness_state,
                {
                    "outcome": "completed",
                    "resolutions": [
                        {"sha": "abc1234", "subject": "Merge auth", "note": "kept both"}
                    ],
                    "skips": [
                        {
                            "sha": "def5678",
                            "subject": "Regenerate fixtures",
                            "note": "empty",
                        }
                    ],
                    "stuck": None,
                },
            )
            rendered = StringIO()
            selected = render_completion_report(
                harness_state=harness_state, console=self._console(rendered)
            )

        assert selected is not None
        output = rendered.getvalue()
        self.assertIn("abc1234 Merge auth", output)
        self.assertIn("kept both", output)
        self.assertIn("def5678 Regenerate fixtures", output)
        self.assertIn("empty", output)
        self.assertNotIn("Rebase Stuck", output)

    def test_returns_none_when_report_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness_state = Path(temp_dir) / "harness-state"
            harness_state.mkdir()
            rendered = StringIO()
            selected = render_completion_report(
                harness_state=harness_state, console=self._console(rendered)
            )

        self.assertIsNone(selected)
        self.assertEqual(rendered.getvalue(), "")

    def test_report_renders_after_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness_state = Path(temp_dir) / "harness-state"
            self._write_report(
                harness_state,
                {
                    "outcome": "completed",
                    "resolutions": [
                        {"sha": "abc1234", "subject": "Merge auth", "note": "kept both"}
                    ],
                    "skips": [],
                    "stuck": None,
                },
            )
            rendered = StringIO()
            console = self._console(rendered)
            summary = UsageSummary.from_totals(
                UsageTotals(
                    input_tokens=10,
                    output_tokens=20,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    total_tokens=30,
                    total_cost=0.1000,
                    wall_clock_ms=0,
                    tool_calls=0,
                    conflicting_commits=0,
                    tool_breakdown=(),
                )
            )
            render_usage_summary("success", summary, console=console)
            _ = render_completion_report(harness_state=harness_state, console=console)

        output = rendered.getvalue()
        self.assertLess(output.index("Grand total"), output.index("Merge auth"))
        self.assertIn("Resolved Conflicts", output)
        self.assertNotIn("INFO", output)
        self.assertNotIn("WARNING", output)


if __name__ == "__main__":
    _ = unittest.main()
