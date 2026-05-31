from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import os
from typing import cast

from pydantic_ai import Agent, AgentRunResult
from pydantic_ai.exceptions import (
    AgentRunError,
    ModelAPIError,
    ModelHTTPError,
    UserError,
)
from pydantic_ai.usage import RunUsage
import structlog
from structlog.stdlib import BoundLogger

from .changelog_models import (
    ConflictReviewSections,
    EvidenceBundle,
    UpstreamNarrativeEvidence,
    UpstreamNarrativeSections,
)
from .opencode_env import OpenCodeEnv

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))


class ChangelogLlmError(RuntimeError):
    """Raised when changelog section generation fails."""


@dataclass(frozen=True)
class UpstreamNarrativeResult:
    """Carry upstream-only section bodies plus usage totals for command-level reporting."""

    sections: UpstreamNarrativeSections
    usage: RunUsage
    estimated_cost: Decimal | None


@dataclass(frozen=True)
class ConflictReviewResult:
    """Carry conflict-review section bodies plus usage totals for command-level reporting."""

    sections: ConflictReviewSections
    usage: RunUsage
    estimated_cost: Decimal | None


UPSTREAM_NARRATIVE_SYSTEM_PROMPT = (
    "You are generating the upstream-only top half of a git integration changelog. "
    "Only use deterministic evidence provided by the caller. "
    "Return markdown with exactly these headings in order:\n"
    "## Summary\n"
    "## Key Change Arcs\n\n"
    "The evidence intentionally excludes fork-side conflict analysis. "
    "Do not infer or describe what the fork changed, what the fork intended, or why the fork might conflict. "
    "In \"## Key Change Arcs\", use an abstraction ladder for each arc:\n"
    "1) Define the arc in plain language.\n"
    "2) Explain it at a conceptual level before technical details.\n"
    "3) Bias toward several paragraphs when evidence supports it.\n"
    "4) Keep the conceptual explanation, then add operator-facing detail about how the change shows up in practice.\n\n"
    "For each arc, prefer paragraph prose over terse bullets. "
    "Describe what the capability is for, where the operator encounters it, and what changes in day-to-day use. "
    "Name concrete user-facing surfaces when the evidence supports them: commands, flags, config keys, files, or UI surfaces. "
    "When configuration exists, name the parameter exactly, describe what it controls, call out defaults, and mention meaningful values or matching semantics. "
    "Do not drift into reviewer guidance unless it directly explains user-visible behavior.\n\n"
    "If you mention repo-local jargon or a feature name, immediately explain what it does in plain English.\n"
    "Do not leave unexplained labels such as internal action names, prompt names, or command names.\n"
    "Prefer general language over source-local jargon. "
    "If technical terms are necessary, define them before using them. "
    "Do not invent files, metrics, or behavior not present in evidence."
)

CONFLICT_REVIEW_SYSTEM_PROMPT = (
    "You are generating the conflict-analysis bottom half of a git integration changelog. "
    "Only use deterministic evidence provided by the caller. "
    "Return markdown with exactly these headings in order:\n"
    "## Conflict Pair Evaluations\n"
    "## Risk and Review Notes\n\n"
    "Do not write \"## Summary\" or \"## Key Change Arcs\".\n"
    "In \"## Conflict Pair Evaluations\", create one subsection per conflict path from the evidence, and include these labels in each subsection:\n"
    "- Fork-side intent\n"
    "- Upstream-side intent\n"
    "- Conceptual relationship\n"
    "- Why this is or is not a conceptual conflict\n"
    "- Merge considerations\n"
    "Describe each side as a feature or behavior, not as a raw evidence dump.\n"
    "Write \"Upstream-side intent\" as a short paragraph, not a single sentence fragment, whenever evidence supports it.\n"
    "If deterministic signals for a path are too sparse to explain exact behavior, explicitly write \"insufficient evidence\" and avoid unsupported claims.\n"
    "Do not restate churn counts, commit sample lists, hunk headers, or truncation metadata in the final markdown unless needed to explain uncertainty.\n"
    "If you mention repo-local jargon or a feature name, immediately explain what it does in plain English.\n"
    "Do not leave unexplained labels such as internal action names, prompt names, or command names.\n"
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


def build_upstream_narrative_prompt(evidence: UpstreamNarrativeEvidence) -> str:
    """Build the upstream-only prompt payload used for the top-half changelog narrative."""

    return _build_json_prompt(
        evidence,
        intro=(
            "Generate an operator-facing upstream-only changelog narrative from this deterministic evidence.\n"
            "Use only this evidence.\n\n"
            "The payload is intentionally sanitized to exclude fork-side conflict evidence.\n"
        ),
    )


def build_conflict_review_prompt(evidence: EvidenceBundle) -> str:
    """Build the full-context prompt payload used for conflict and review sections only."""

    return _build_json_prompt(
        evidence,
        intro=(
            "Generate conflict pair evaluations and review notes from this deterministic evidence.\n"
            "Use only this evidence.\n\n"
            "Conflict side comparisons and any truncation metadata are authoritative inputs; synthesize them into feature-level summaries without repeating the raw evidence structure.\n"
        ),
    )


def _build_json_prompt(
    payload: UpstreamNarrativeEvidence | EvidenceBundle,
    *,
    intro: str,
) -> str:
    """Serialize deterministic evidence into the JSON prompt wrapper shared by both agents."""

    payload_dict = asdict(payload) if isinstance(payload, UpstreamNarrativeEvidence) else asdict(payload)
    formatted = json.dumps(payload_dict, indent=2, sort_keys=True)
    return f"{intro}\nEvidence JSON:\n```json\n{formatted}\n```"


def resolve_agent_model(env: OpenCodeEnv) -> str:
    """Resolve model identifier used for pydantic-ai changelog generation."""

    model = (env.model or "").strip()
    if not model:
        raise ChangelogLlmError(
            "OPENCODE_MODEL must be set in OpenCode env for `forklift changelog` generation."
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
def provider_env_from_opencode(env: OpenCodeEnv) -> Generator[None, None, None]:
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


async def generate_upstream_narrative(
    evidence: UpstreamNarrativeEvidence,
    env: OpenCodeEnv,
) -> UpstreamNarrativeResult:
    """Generate upstream-only section bodies plus usage totals via pydantic-ai."""

    call_result = await _run_markdown_generation(
        prompt=build_upstream_narrative_prompt(evidence),
        system_prompt=UPSTREAM_NARRATIVE_SYSTEM_PROMPT,
        operation="upstream narrative",
        env=env,
    )
    sections = _extract_section_bodies(
        call_result.markdown,
        ("## Summary", "## Key Change Arcs"),
    )
    return UpstreamNarrativeResult(
        sections=UpstreamNarrativeSections(
            summary_markdown=sections["## Summary"],
            key_change_arcs_markdown=sections["## Key Change Arcs"],
        ),
        usage=call_result.usage,
        estimated_cost=call_result.estimated_cost,
    )


async def generate_conflict_review(
    evidence: EvidenceBundle,
    env: OpenCodeEnv,
) -> ConflictReviewResult:
    """Generate conflict-analysis section bodies plus usage totals via pydantic-ai."""

    call_result = await _run_markdown_generation(
        prompt=build_conflict_review_prompt(evidence),
        system_prompt=CONFLICT_REVIEW_SYSTEM_PROMPT,
        operation="conflict review",
        env=env,
    )
    sections = _extract_section_bodies(
        call_result.markdown,
        ("## Conflict Pair Evaluations", "## Risk and Review Notes"),
    )
    return ConflictReviewResult(
        sections=ConflictReviewSections(
            conflict_pair_evaluations_markdown=sections["## Conflict Pair Evaluations"],
            risk_and_review_notes_markdown=sections["## Risk and Review Notes"],
        ),
        usage=call_result.usage,
        estimated_cost=call_result.estimated_cost,
    )


@dataclass(frozen=True)
class _MarkdownGenerationResult:
    """Store raw markdown plus usage metadata before section parsing happens."""

    markdown: str
    usage: RunUsage
    estimated_cost: Decimal | None


async def _run_markdown_generation(
    *,
    prompt: str,
    system_prompt: str,
    operation: str,
    env: OpenCodeEnv,
) -> _MarkdownGenerationResult:
    """Run one pydantic-ai markdown generation call with shared env and error handling."""

    model_name = resolve_agent_model(env)
    try:
        with provider_env_from_opencode(env):
            agent: Agent[None, str] = Agent(model_name, system_prompt=system_prompt)
            result: AgentRunResult[str] = await agent.run(prompt)
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
        raise ChangelogLlmError("Changelog model returned empty markdown output.")
    estimated_cost: Decimal | None = None
    try:
        estimated_cost = result.response.cost().total_price
    except LookupError as exc:
        logger.warning(
            "Unable to estimate changelog model cost",
            operation=operation,
            model=model_name,
            error=str(exc),
        )
    raw_usage = cast(object, result.usage)
    if not isinstance(raw_usage, RunUsage):
        raise ChangelogLlmError(
            f"Changelog model returned unexpected usage payload type: {type(raw_usage).__name__}"
        )
    return _MarkdownGenerationResult(
        markdown=output,
        usage=raw_usage,
        estimated_cost=estimated_cost,
    )


def _extract_section_bodies(markdown: str, headings: tuple[str, ...]) -> dict[str, str]:
    """Split generated markdown into required section bodies for host-side assembly."""

    seen: list[str] = []
    section_lines: dict[str, list[str]] = {heading: [] for heading in headings}
    current_heading: str | None = None

    for line in markdown.strip().splitlines():
        if line in section_lines:
            seen.append(line)
            current_heading = line
            continue
        if current_heading is not None:
            section_lines[current_heading].append(line)

    if tuple(seen) != headings:
        raise ChangelogLlmError(
            (
                "Changelog model returned headings out of contract order: "
                f"expected {headings}, got {tuple(seen)}"
            )
        )

    normalized: dict[str, str] = {}
    for heading in headings:
        body = "\n".join(section_lines[heading]).strip()
        if not body:
            raise ChangelogLlmError(
                f"Changelog model returned an empty section body for {heading}."
            )
        normalized[heading] = body
    return normalized
