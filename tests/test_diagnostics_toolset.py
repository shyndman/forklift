"""Tests for the diagnostics toolset (`forklift_harness.diagnostics_toolset`).

The `report_tool_issue` tool is fire-and-forget QA telemetry: it appends one
JSON object per call to ``harness_state_dir/tool-issues.jsonl`` and must never
disturb the rebase. These tests pin the on-disk shape, append semantics, and
input validation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from forklift_harness.agent_deps import AgentDeps, RunReport
from forklift_harness.diagnostics_toolset import DiagnosticsToolset
from forklift_harness.rebase_state import HarnessConfig, RebaseState

REAL_GIT = shutil.which("git") or "/usr/bin/git"


def _config(workspace: Path, harness_state: Path) -> HarnessConfig:
    harness_state.mkdir(parents=True, exist_ok=True)
    return HarnessConfig(
        workspace_dir=workspace,
        harness_state_dir=harness_state,
        real_git_bin=REAL_GIT,
        main_branch="main",
        upstream_ref="upstream/main",
        continue_check_file=harness_state / "rebase-continue-check.sh",
        agent_lifetime="conflict",
        conflict_index_snapshot=harness_state / "conflict-index",
    )


def _deps(tmp_path: Path) -> AgentDeps:
    config = _config(tmp_path / "workspace", tmp_path / "harness_state")
    return AgentDeps(state=RebaseState(config), config=config, report=RunReport())


def _ctx(deps: AgentDeps) -> RunContext[AgentDeps]:
    # report_tool_issue only reads ctx.deps; a namespace is sufficient.
    return cast(RunContext[AgentDeps], cast(object, SimpleNamespace(deps=deps)))


def _issues_file(deps: AgentDeps) -> Path:
    return deps.config.harness_state_dir / "tool-issues.jsonl"


def test_only_report_tool_issue_is_exposed() -> None:
    assert set(DiagnosticsToolset().tools) == {"report_tool_issue"}


def test_call_records_one_jsonl_line_round_tripping_payload(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    toolset = DiagnosticsToolset()
    body = "x" * 5000
    description = f"Truncated output: expected full file, got a cut-off chunk.\n{body}"

    result = toolset.report_tool_issue(_ctx(deps), "read", description)

    assert "read" in result
    lines = _issues_file(deps).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = cast(dict[str, object], json.loads(lines[0]))
    assert isinstance(record, dict)
    assert record["tool"] == "read"
    assert record["description"] == description
    assert isinstance(record["ts"], float)


def test_successive_calls_append_in_order(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    toolset = DiagnosticsToolset()

    _ = toolset.report_tool_issue(_ctx(deps), "read", "first issue")
    _ = toolset.report_tool_issue(_ctx(deps), "edit", "second issue")

    lines = _issues_file(deps).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = cast(dict[str, object], json.loads(lines[0]))
    second = cast(dict[str, object], json.loads(lines[1]))
    assert (first["tool"], first["description"]) == ("read", "first issue")
    assert (second["tool"], second["description"]) == ("edit", "second issue")


def test_blank_tool_is_rejected_without_writing(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    toolset = DiagnosticsToolset()

    with pytest.raises(ModelRetry):
        _ = toolset.report_tool_issue(_ctx(deps), "   ", "something went wrong")

    issues = _issues_file(deps)
    assert not issues.exists() or issues.read_text(encoding="utf-8") == ""


def test_blank_description_is_rejected_without_writing(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    toolset = DiagnosticsToolset()

    with pytest.raises(ModelRetry):
        _ = toolset.report_tool_issue(_ctx(deps), "read", "  \n  ")

    issues = _issues_file(deps)
    assert not issues.exists() or issues.read_text(encoding="utf-8") == ""
