from __future__ import annotations

import json
from io import StringIO
import tempfile
import unittest
from pathlib import Path

from rich.console import Console

from forklift.post_run_metrics import (
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

    def test_parses_cost_totals_and_final_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = self._write_log(
                root,
                [
                    "2026-03-01T00:00:00Z harness: boot",
                    json.dumps({"type": "step_start", "part": {"cost": 999}}),
                    json.dumps(
                        {
                            "type": "step_finish",
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
                    "{bad-json",
                    json.dumps(
                        {
                            "type": "step_finish",
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

            summary = parse_usage_summary(log_path)

        self.assertTrue(summary.available)
        assert summary.totals is not None
        self.assertEqual(summary.totals.input_tokens, 100)
        self.assertEqual(summary.totals.output_tokens, 50)
        self.assertEqual(summary.totals.reasoning_tokens, 25)
        self.assertEqual(summary.totals.cache_read_tokens, 6)
        self.assertEqual(summary.totals.total_tokens, 181)
        self.assertAlmostEqual(summary.totals.total_cost, 0.5)

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

            summary = parse_usage_summary(log_path)

        self.assertTrue(summary.available)
        assert summary.totals is not None
        self.assertEqual(summary.totals.input_tokens, 0)
        self.assertEqual(summary.totals.output_tokens, 0)
        self.assertEqual(summary.totals.reasoning_tokens, 0)
        self.assertEqual(summary.totals.cache_read_tokens, 0)
        self.assertEqual(summary.totals.total_tokens, 99)
        self.assertAlmostEqual(summary.totals.total_cost, 0.2)

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

            summary = parse_usage_summary(log_path)

        self.assertFalse(summary.available)
        self.assertEqual(summary.reason_unavailable, "no usage events found")


class RenderUsageSummaryTests(unittest.TestCase):
    def _console(self, buffer: StringIO) -> Console:
        return Console(file=buffer, force_terminal=False, color_system=None, width=80)

    def test_renders_rows_in_expected_order_and_right_aligned_column(self) -> None:
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
                )
            ),
            console=self._console(rendered),
        )
        output = rendered.getvalue()

        expected_values = {
            "Input": "1",
            "Output": "22",
            "Reasoning": "333",
            "Cache read": "4",
            "Total tokens": "360",
            "Total cost": "$0.1200",
        }
        row_lines: dict[str, str] = {}
        for line in output.splitlines():
            for label in expected_values:
                if line.startswith(label):
                    row_lines[label] = line

        self.assertEqual(
            list(row_lines),
            [
                "Input",
                "Output",
                "Reasoning",
                "Cache read",
                "Total tokens",
                "Total cost",
            ],
        )
        right_edges: set[int] = set()
        for label, value in expected_values.items():
            line = row_lines[label]
            self.assertTrue(line.endswith(value))
            right_edges.add(line.index(value) + len(value))
        self.assertEqual(len(right_edges), 1)

    def test_formats_numbers_and_unavailable_path(self) -> None:
        totals_summary = UsageSummary.from_totals(
            UsageTotals(
                input_tokens=1234,
                output_tokens=56,
                reasoning_tokens=7,
                cache_read_tokens=8,
                total_tokens=1305,
                total_cost=0.6562,
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
        return Console(file=buffer, force_terminal=False, color_system=None, width=80)

    def test_prefers_stuck_report_over_done(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _ = (workspace / "DONE.md").write_text("# Done\n\nDone body", encoding="utf-8")
            _ = (workspace / "STUCK.md").write_text("# Stuck\n\n**Investigate**", encoding="utf-8")

            rendered = StringIO()
            selected = render_completion_report(workspace, console=self._console(rendered))

        assert selected is not None
        self.assertEqual(selected.name, "STUCK.md")
        output = rendered.getvalue()
        self.assertIn("Stuck", output)
        self.assertIn("Investigate", output)
        self.assertNotIn("Done body", output)

    def test_falls_back_to_done_and_skips_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _ = (workspace / "DONE.md").write_text("# Done\n\nDone body", encoding="utf-8")

            rendered = StringIO()
            selected = render_completion_report(workspace, console=self._console(rendered))

        assert selected is not None
        self.assertEqual(selected.name, "DONE.md")
        self.assertIn("Done body", rendered.getvalue())

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            rendered = StringIO()
            selected = render_completion_report(workspace, console=self._console(rendered))

        self.assertIsNone(selected)
        self.assertEqual(rendered.getvalue(), "")

    def test_report_renders_after_metrics_with_markdown_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _ = (workspace / "DONE.md").write_text("# Final report\n\n**All done**", encoding="utf-8")

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
                )
            )
            render_usage_summary("success", summary, console=console)
            _ = render_completion_report(workspace, console=console)

        output = rendered.getvalue()
        self.assertLess(output.index("Grand total"), output.index("Final report"))
        self.assertIn("All done", output)
        self.assertNotIn("**All done**", output)
        self.assertNotIn("INFO", output)
        self.assertNotIn("WARNING", output)


if __name__ == "__main__":
    _ = unittest.main()
