"""Post-run summary for the in-container rebase run: exact cost + outcome counts.

The harness writes ``usage.json`` (aggregated token counts + the configured model
id) and ``rebase-report.json`` (resolutions/skips/outcome). The host prices the
run once via the models.dev catalog -- a single model per run makes the aggregate
price exact -- and emits one structured summary event at the top-level ``run=<id>``
stream the operator sees, replacing the retired OpenCode log parser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

from .models_dev import Catalog, load_catalog, price_tokens
from structlog.stdlib import BoundLogger

USAGE_FILE_NAME = "usage.json"
REBASE_REPORT_FILE_NAME = "rebase-report.json"


@dataclass(frozen=True)
class RunSummary:
    """Finalized per-run telemetry surfaced at the top level once a run ends."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    requests: int
    tool_calls: int
    cost_usd: Decimal | None
    conflicts_resolved: int
    skips: int
    outcome: str
    duration_s: float


def build_run_summary(
    harness_state: Path,
    *,
    outcome: str,
    duration_s: float,
    catalog: Catalog | None = None,
) -> RunSummary:
    """Assemble the run summary from the harness usage + rebase-report artifacts."""

    usage = _load_json(harness_state / USAGE_FILE_NAME)
    report = _load_json(harness_state / REBASE_REPORT_FILE_NAME)
    return RunSummary(
        model=_str(usage, "model"),
        input_tokens=_int(usage, "input_tokens"),
        output_tokens=_int(usage, "output_tokens"),
        cache_read_tokens=_int(usage, "cache_read_tokens"),
        cache_write_tokens=_int(usage, "cache_write_tokens"),
        total_tokens=_int(usage, "total_tokens"),
        requests=_int(usage, "requests"),
        tool_calls=_int(usage, "tool_calls"),
        cost_usd=_price(usage, catalog),
        conflicts_resolved=_count(report, "resolutions"),
        skips=_count(report, "skips"),
        outcome=outcome,
        duration_s=round(duration_s, 3),
    )


def emit_run_summary(logger: BoundLogger, summary: RunSummary) -> None:
    """Emit the run summary as one structured event on the top-level stream."""

    logger.info(
        "run summary",
        model=summary.model,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        cache_read_tokens=summary.cache_read_tokens,
        cache_write_tokens=summary.cache_write_tokens,
        total_tokens=summary.total_tokens,
        requests=summary.requests,
        tool_calls=summary.tool_calls,
        cost_usd=str(summary.cost_usd) if summary.cost_usd is not None else None,
        conflicts_resolved=summary.conflicts_resolved,
        skips=summary.skips,
        outcome=summary.outcome,
        duration_s=summary.duration_s,
    )


def _price(usage: dict[str, object], catalog: Catalog | None) -> Decimal | None:
    """Price the aggregated usage once; return ``None`` when the model is unpriceable.

    The catalog is loaded lazily so runs without a recorded model never touch the
    network. Unknown providers/models price to ``None`` (see ``models_dev``).
    """

    model = usage.get("model")
    if not isinstance(model, str) or not model:
        return None
    pricing = load_catalog() if catalog is None else catalog
    return price_tokens(
        pricing,
        model,
        input_tokens=_int(usage, "input_tokens"),
        output_tokens=_int(usage, "output_tokens"),
        cache_read_tokens=_int(usage, "cache_read_tokens"),
        cache_write_tokens=_int(usage, "cache_write_tokens"),
    )


def _load_json(path: Path) -> dict[str, object]:
    try:
        parsed: object = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}


def _int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _count(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    return len(cast(list[object], value)) if isinstance(value, list) else 0
