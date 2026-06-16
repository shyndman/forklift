"""Tests for agent construction, model/effort wiring, and the system prompt.

Deterministic only (TestModel, no provider): proves the agent builds with and
without code mode, exposes the expected tools, completes a trivial run, maps
reasoning effort to portable ``ModelSettings.thinking``, and that the system prompt
states the inverted ``theirs``/``ours`` rebase semantics. Live-provider resolution
quality is verified separately (see the live smoke for tasks 3.1a/3.1ba).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings

from forklift_harness.agent import (
    DEFAULT_MODEL,
    build_agent,
    model_settings_for_effort,
    resolve_model,
)
from forklift_harness.agent_deps import AgentDeps, RunReport
from forklift_harness.rebase_state import HarnessConfig, RebaseState
from forklift_harness.system_prompt import SYSTEM_PROMPT

REAL_GIT = shutil.which("git") or "/usr/bin/git"


def _deps(tmp_path: Path) -> AgentDeps:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = HarnessConfig(
        workspace_dir=workspace,
        harness_state_dir=tmp_path / "state",
        real_git_bin=REAL_GIT,
        main_branch="main",
        upstream_ref="upstream/main",
        continue_check_file=tmp_path / "state" / "check.sh",
        agent_lifetime="conflict",
    )
    return AgentDeps(state=RebaseState(config), config=config, report=RunReport())


# --------------------------------------------------------------------------- #
# construction
# --------------------------------------------------------------------------- #


def test_builds_with_code_mode() -> None:
    agent = build_agent(model=TestModel(call_tools=[]), code_mode=True)
    assert agent is not None


def test_exposes_git_and_file_tools_without_code_mode() -> None:
    agent = build_agent(model=TestModel(call_tools=[]), code_mode=False)
    names: set[str] = set()
    for toolset in agent.toolsets:
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict):
            names.update(cast("dict[str, object]", tools).keys())
    assert {"run_command", "read_file", "write_file", "edit_file"} <= names


def test_trivial_run_completes(tmp_path: Path) -> None:
    agent = build_agent(
        model=TestModel(call_tools=[], custom_output_text="ok"), code_mode=False
    )
    result = agent.run_sync("say hello", deps=_deps(tmp_path))
    assert result.output == "ok"


# --------------------------------------------------------------------------- #
# model + effort resolution
# --------------------------------------------------------------------------- #


def test_resolve_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORKLIFT_MODEL", raising=False)
    assert resolve_model() == DEFAULT_MODEL


def test_resolve_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORKLIFT_MODEL", "openai:gpt-5")
    assert resolve_model() == "openai:gpt-5"


def test_resolve_model_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORKLIFT_MODEL", "openai:gpt-5")
    assert resolve_model("anthropic:claude") == "anthropic:claude"


def test_effort_maps_to_thinking() -> None:
    for level in ("minimal", "low", "medium", "high", "xhigh"):
        settings = model_settings_for_effort(level)
        assert settings == ModelSettings(thinking=level)  # type: ignore[typeddict-item]


def test_unknown_effort_is_none() -> None:
    assert model_settings_for_effort(None) is None
    assert model_settings_for_effort("") is None
    assert model_settings_for_effort("turbo") is None


# --------------------------------------------------------------------------- #
# system prompt: inverted theirs/ours (design Decision 10)
# --------------------------------------------------------------------------- #


def test_system_prompt_states_inverted_sides() -> None:
    prompt = SYSTEM_PROMPT.lower()
    assert "ours" in prompt and "theirs" in prompt
    # ours = upstream, theirs = fork -- the critical inversion must be explicit.
    assert "`ours`  = the upstream" in prompt
    assert "`theirs` = the fork" in prompt


def test_system_prompt_lists_mediated_vocabulary() -> None:
    assert "git rebase --continue --resolution-note" in SYSTEM_PROMPT
    assert "git rebase --skip --resolution-note" in SYSTEM_PROMPT
    assert "git rebase --abort --reason" in SYSTEM_PROMPT
    assert "git reset-conflict" in SYSTEM_PROMPT
