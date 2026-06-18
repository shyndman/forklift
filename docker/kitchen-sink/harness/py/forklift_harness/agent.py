"""Construct the in-process Pydantic AI conflict-resolution agent.

Wires the capability set the conflict-resolution agent needs:

* the forklift git-mediation toolset (:class:`~forklift_harness.toolset.ForkliftGitToolset`),
* a home-rolled file toolset (read/write/edit, workspace-scoped) standing in for
  the harness ``FileSystem`` capability, which the released ``pydantic-ai-harness``
  (0.3.0) does not ship, and
* ``CodeMode`` (the released harness capability), wrapping the tools into a
  sandboxed ``run_code``.

Model and reasoning effort come from the environment (``FORKLIFT_MODEL`` /
``FORKLIFT_MODEL_EFFORT``); provider API keys are read directly by Pydantic AI
(``OPENROUTER_API_KEY``, ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, and for Google
``GOOGLE_API_KEY`` with ``GEMINI_API_KEY`` fallback).
"""

from __future__ import annotations

import os
from typing import cast, get_args

from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings, ThinkingLevel
from pydantic_ai_harness import CodeMode

from .agent_deps import AgentDeps
from .diagnostics_toolset import DiagnosticsToolset
from .file_toolset import FileToolset
from .system_prompt import SYSTEM_PROMPT
from .toolset import ForkliftGitToolset

# Default model: OpenRouter-hosted Gemini, using the OPENROUTER_API_KEY the
# operator's forklift.env already defines.
DEFAULT_MODEL = "openrouter:google/gemini-3-flash-preview"

# Valid reasoning-effort levels (the Literal arm of pydantic-ai's ThinkingLevel).
_EFFORT_LEVELS: frozenset[str] = frozenset(
    cast(tuple[str, ...], get_args(get_args(ThinkingLevel)[1]))
)


def resolve_model(model: str | Model | None = None) -> str | Model:
    """Resolve the model id, falling back to ``FORKLIFT_MODEL`` then the default."""

    if model is not None:
        return model
    return os.environ.get("FORKLIFT_MODEL") or DEFAULT_MODEL


def model_settings_for_effort(effort: str | None) -> ModelSettings | None:
    """Map a reasoning-effort level to provider-portable ``ModelSettings.thinking``.

    Returns ``None`` for an unset or unrecognized effort (provider defaults apply).
    The ``thinking`` field is the unified knob Pydantic AI translates to each
    provider's native reasoning/thinking setting.
    """

    if effort and effort in _EFFORT_LEVELS:
        return ModelSettings(thinking=cast(ThinkingLevel, effort))
    return None


def build_agent(
    *,
    model: str | Model | None = None,
    effort: str | None = None,
    code_mode: bool = True,
) -> Agent[AgentDeps, str]:
    """Build the conflict-resolution agent.

    ``model`` overrides ``FORKLIFT_MODEL`` (tests inject a ``TestModel`` /
    ``FunctionModel``); ``effort`` overrides ``FORKLIFT_MODEL_EFFORT``. ``code_mode``
    wraps the toolset in the Monty ``run_code`` sandbox (disabled in tests that
    drive scripted tool calls directly).
    """

    resolved_model = resolve_model(model)
    resolved_effort = (
        effort if effort is not None else os.environ.get("FORKLIFT_MODEL_EFFORT")
    )
    settings = model_settings_for_effort(resolved_effort)
    capabilities: list[AbstractCapability[AgentDeps]] = (
        [CodeMode[AgentDeps]()] if code_mode else []
    )

    return Agent(
        resolved_model,
        deps_type=AgentDeps,
        output_type=str,
        system_prompt=SYSTEM_PROMPT,
        toolsets=[ForkliftGitToolset(), FileToolset(), DiagnosticsToolset()],
        capabilities=capabilities,
        model_settings=settings,
    )
