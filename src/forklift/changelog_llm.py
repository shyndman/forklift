from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
import json
import os

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
    "## Key Change Arcs\n"
    "## Conflict Pair Evaluations\n"
    "## Risk and Review Notes\n\n"
    "In \"## Key Change Arcs\", use an abstraction ladder for each arc:\n"
    "1) Define the arc in plain language.\n"
    "2) Explain it at a conceptual level before technical details.\n"
    "3) Bias toward several paragraphs when evidence supports it.\n\n"
    "In \"## Conflict Pair Evaluations\", create one subsection per conflict path from the evidence, and include these labels in each subsection:\n"
    "- Fork-side intent\n"
    "- Upstream-side intent\n"
    "- Conceptual relationship\n"
    "- Merge discussion starters\n"
    "If deterministic signals for a path are too sparse, explicitly write \"insufficient evidence\" and avoid unsupported claims.\n\n"
    "Prefer general language over source-local jargon. "
    "If technical terms are necessary, define them before using them. "
    "Do not invent files, metrics, conflicts, or behavior not present in evidence."
)

PROVIDER_ENV_MAPPINGS = (
    ("openai_api_key", "OPENAI_API_KEY"),
    ("anthropic_api_key", "ANTHROPIC_API_KEY"),
    ("openrouter_api_key", "OPENROUTER_API_KEY"),
    ("google_generative_ai_api_key", "GOOGLE_API_KEY"),
    ("google_generative_ai_api_key", "GEMINI_API_KEY"),
)
PROVIDER_MODEL_ALIASES = {
    "google": "google-gla",
}


def build_narrative_prompt(evidence: EvidenceBundle) -> str:
    """Build a bounded narrative prompt from deterministic changelog evidence only."""

    payload = asdict(evidence)
    formatted = json.dumps(payload, indent=2, sort_keys=True)
    return (
        "Generate an operator-facing changelog narrative from this deterministic evidence.\n"
        "Use only this evidence.\n\n"
        "Conflict side comparisons and any truncation metadata are authoritative; do not infer beyond them.\n\n"
        f"Evidence JSON:\n```json\n{formatted}\n```"
    )


def resolve_agent_model(env: OpenCodeEnv) -> str:
    """Resolve model identifier used for pydantic-ai narrative generation."""

    model = (env.model or "").strip()
    if not model:
        raise ChangelogLlmError(
            "OPENCODE_MODEL must be set in OpenCode env for `forklift changelog` narrative generation."
        )
    if ":" in model:
        return model

    if "/" in model:
        provider, model_name = model.split("/", 1)
        if provider and model_name:
            normalized_provider = PROVIDER_MODEL_ALIASES.get(provider, provider)
            return f"{normalized_provider}:{model_name}"

    return model


@contextmanager
def provider_env_from_opencode(env: OpenCodeEnv) -> Iterator[None]:
    """Temporarily bridge OpenCode provider keys into env vars expected by pydantic-ai."""

    sentinel = object()
    previous: dict[str, object] = {}
    values_by_attr = {
        "openai_api_key": env.openai_api_key,
        "anthropic_api_key": env.anthropic_api_key,
        "openrouter_api_key": env.openrouter_api_key,
        "google_generative_ai_api_key": env.google_generative_ai_api_key,
    }
    for attr_name, env_name in PROVIDER_ENV_MAPPINGS:
        value = values_by_attr[attr_name]
        if not value:
            continue
        previous[env_name] = os.environ.get(env_name, sentinel)
        os.environ[env_name] = value

    try:
        yield
    finally:
        for env_name, previous_value in previous.items():
            if previous_value is sentinel:
                _ = os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = str(previous_value)


async def generate_changelog_narrative(evidence: EvidenceBundle, env: OpenCodeEnv) -> str:
    """Generate markdown narrative text from deterministic evidence via pydantic-ai."""

    model_name = resolve_agent_model(env)
    prompt = build_narrative_prompt(evidence)
    try:
        with provider_env_from_opencode(env):
            agent = Agent(model_name, system_prompt=NARRATIVE_SYSTEM_PROMPT)
            result = await agent.run(prompt)
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
