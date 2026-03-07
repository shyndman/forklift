from __future__ import annotations

from dataclasses import asdict
import json

from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    AgentRunError,
    ModelAPIError,
    ModelHTTPError,
    UserError,
)

from .changelog_models import EvidenceBundle
from .opencode_env import OpenCodeEnv


class ChangelogLlmError(RuntimeError):
    """Raised when changelog narrative generation fails."""


NARRATIVE_SYSTEM_PROMPT = (
    "You are generating a git integration changelog. "
    "Only use deterministic evidence provided by the caller. "
    "Return markdown with exactly these headings in order:\n"
    "## Summary\n"
    "## Notable Change Themes\n"
    "## Risk and Review Notes"
)


def build_narrative_prompt(evidence: EvidenceBundle) -> str:
    """Build a bounded narrative prompt from deterministic changelog evidence only."""

    payload = asdict(evidence)
    formatted = json.dumps(payload, indent=2, sort_keys=True)
    return (
        "Generate an operator-facing changelog narrative from this deterministic evidence.\n"
        "Do not invent files, metrics, or conflicts that are absent from evidence.\n"
        "Keep language concise and actionable.\n\n"
        f"Evidence JSON:\n```json\n{formatted}\n```"
    )


def resolve_agent_model(env: OpenCodeEnv) -> str:
    """Resolve model identifier used for pydantic-ai narrative generation."""

    model = (env.model or "").strip()
    if not model:
        raise ChangelogLlmError(
            "OPENCODE_MODEL must be set in OpenCode env for `forklift changelog` narrative generation."
        )
    return model


def generate_changelog_narrative(evidence: EvidenceBundle, env: OpenCodeEnv) -> str:
    """Generate markdown narrative text from deterministic evidence via pydantic-ai."""

    model_name = resolve_agent_model(env)
    prompt = build_narrative_prompt(evidence)
    agent = Agent(model_name, system_prompt=NARRATIVE_SYSTEM_PROMPT)

    try:
        result = agent.run_sync(prompt)
    except UserError as exc:
        raise ChangelogLlmError(f"Changelog model configuration error: {exc}") from exc
    except ModelHTTPError as exc:
        raise ChangelogLlmError(
            f"Changelog model HTTP failure ({exc.status_code}) for {exc.model_name}: {exc}"
        ) from exc
    except ModelAPIError as exc:
        raise ChangelogLlmError(
            f"Changelog model API failure for {exc.model_name}: {exc}"
        ) from exc
    except AgentRunError as exc:
        raise ChangelogLlmError(f"Changelog model runtime failure: {exc}") from exc
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise ChangelogLlmError(f"Unexpected changelog model failure: {exc}") from exc

    output = result.output.strip()
    if not output:
        raise ChangelogLlmError("Changelog model returned empty narrative output.")
    return output
