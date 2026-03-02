from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from forklift.clientlog import ClientLogParser, Clientlog, TranscriptRenderer
from forklift.clientlog_command import is_terminal_run_state, resolve_run_dir


class ClientlogCommandTests(unittest.TestCase):
    def test_requires_run_state_before_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir)
            run_dir = runs_root / "sample-run"
            harness_state = run_dir / "harness-state"
            harness_state.mkdir(parents=True)
            _ = (harness_state / "opencode-client.log").write_text(
                "2026-03-02T11:01:37+00:00 Agent Starting...\n"
            )

            command = Clientlog(run_id="sample-run", follow=False)
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()

            with patch("forklift.clientlog.DEFAULT_RUNS_ROOT", runs_root):
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    with self.assertRaises(SystemExit) as ctx:
                        asyncio.run(command.run())

            self.assertNotEqual(ctx.exception.code, 0)
            self.assertIn("required run-state metadata is missing", str(ctx.exception))
            self.assertEqual(stdout_capture.getvalue(), "")

    def test_renders_pending_step_with_tool_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir)
            run_dir = runs_root / "sample-run"
            harness_state = run_dir / "harness-state"
            harness_state.mkdir(parents=True)
            _ = (run_dir / "run-state.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "run_id": "ABCD",
                        "prepared_at": "2026-03-02T11:01:37+00:00",
                    }
                )
                + "\n"
            )

            log_payload = "\n".join(
                [
                    "2026-03-02T11:01:37+00:00 Agent Starting...",
                    json.dumps(
                        {
                            "type": "step_start",
                            "timestamp": 1772449302239,
                            "part": {
                                "id": "prt-1",
                                "messageID": "msg-1",
                                "type": "step-start",
                                "snapshot": "abc123",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "tool_use",
                            "timestamp": 1772449302439,
                            "part": {
                                "id": "prt-2",
                                "messageID": "msg-1",
                                "type": "tool",
                                "callID": "call-1",
                                "tool": "bash",
                                "state": {
                                    "status": "completed",
                                    "input": {
                                        "description": "show output",
                                        "command": "echo hello",
                                    },
                                    "output": "hello\nworld",
                                },
                            },
                        }
                    ),
                ]
            )
            _ = (harness_state / "opencode-client.log").write_text(log_payload + "\n")

            command = Clientlog(run_id="sample-run", follow=False)
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()

            with patch("forklift.clientlog.DEFAULT_RUNS_ROOT", runs_root):
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    asyncio.run(command.run())

            rendered = stdout_capture.getvalue()
            self.assertIn("Step msg-1 • pending", rendered)
            self.assertIn("part=prt-1", rendered)
            self.assertIn("tool bash part=prt-2 call=call-1", rendered)
            self.assertIn("hello", rendered)
            self.assertEqual(stderr_capture.getvalue(), "")


class ClientlogParserTests(unittest.TestCase):
    def test_parser_tracks_relative_time_and_follow_rendering(self) -> None:
        parser = ClientLogParser()
        renderer = TranscriptRenderer()

        history_chunk = "\n".join(
            [
                "2026-03-02T11:01:37+00:00 Agent Starting...",
                json.dumps(
                    {
                        "type": "step_start",
                        "timestamp": 1772449302239,
                        "part": {
                            "id": "prt-1",
                            "messageID": "msg-1",
                            "type": "step-start",
                        },
                    }
                ),
            ]
        )
        history_events = parser.feed(history_chunk + "\n")

        self.assertEqual(history_events[0].relative_ms, 0)
        self.assertGreaterEqual(history_events[1].relative_ms, 0)

        follow_state = renderer.initialize_follow_state(history_events)
        new_events = parser.feed(
            json.dumps(
                {
                    "type": "step_finish",
                    "timestamp": 1772449302439,
                    "part": {
                        "id": "prt-2",
                        "messageID": "msg-1",
                        "type": "step-finish",
                        "reason": "done",
                    },
                }
            )
            + "\n"
        )
        follow_render = renderer.render_follow_events(new_events, follow_state)

        self.assertIn("Step msg-1 • completed • live", follow_render)
        self.assertIn("part=prt-2", follow_render)


class ClientlogCommandHelperTests(unittest.TestCase):
    def test_resolve_run_dir_reports_missing_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir)
            with self.assertRaises(SystemExit) as ctx:
                _ = resolve_run_dir("missing-run", runs_root=runs_root)
            self.assertIn("run directory 'missing-run' not found", str(ctx.exception))

    def test_is_terminal_run_state_uses_loaded_status(self) -> None:
        state_file = Path("/tmp/unused-run-state.json")
        self.assertTrue(
            is_terminal_run_state(
                state_file,
                load_required_run_state_fn=lambda _path: {"status": "completed"},
            )
        )
        self.assertFalse(
            is_terminal_run_state(
                state_file,
                load_required_run_state_fn=lambda _path: {"status": "running"},
            )
        )


if __name__ == "__main__":
    _ = unittest.main()
