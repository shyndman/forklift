"""Tests for the host-side post-run summary (`forklift.run_summary`).

Replaces the retired OpenCode-log parser tests: the summary is now sourced from
the harness ``usage.json`` + ``rebase-report.json`` and priced once against the
models.dev catalog (exact for a single-model run).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from forklift.models_dev import Catalog
from forklift.run_summary import build_run_summary, emit_run_summary

_CATALOG: Catalog = {
    "openrouter": {
        "models": {
            "google/gemini-2.5-flash": {
                "cost": {
                    "input": 0.3,
                    "output": 2.5,
                    "cache_read": 0.03,
                    "cache_write": 0.3,
                }
            }
        }
    }
}


def _write(
    harness_state: Path, usage: dict[str, object], report: dict[str, object]
) -> None:
    harness_state.mkdir(parents=True, exist_ok=True)
    _ = (harness_state / "usage.json").write_text(json.dumps(usage), encoding="utf-8")
    _ = (harness_state / "rebase-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )


def _usage(model: str) -> dict[str, object]:
    return {
        "model": model,
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 200,
        "cache_write_tokens": 100,
        "total_tokens": 1500,
        "requests": 3,
        "tool_calls": 4,
    }


def _report() -> dict[str, object]:
    return {
        "outcome": "completed",
        "resolutions": [{"sha": "a", "subject": "s", "note": "n"}, {}],
        "skips": [{}],
        "stuck": None,
    }


def test_summary_carries_token_counts_and_counts(tmp_path: Path) -> None:
    state = tmp_path / "harness-state"
    _write(state, _usage("google:gemini-2.5-flash"), _report())

    summary = build_run_summary(
        state, outcome="success", duration_s=12.3456, catalog=_CATALOG
    )

    assert summary.input_tokens == 1000
    assert summary.output_tokens == 500
    assert summary.cache_read_tokens == 200
    assert summary.cache_write_tokens == 100
    assert summary.total_tokens == 1500
    assert summary.requests == 3
    assert summary.tool_calls == 4
    assert summary.conflicts_resolved == 2
    assert summary.skips == 1
    assert summary.outcome == "success"
    assert summary.duration_s == 12.346


def test_known_model_prices_to_decimal(tmp_path: Path) -> None:
    state = tmp_path / "harness-state"
    _write(state, _usage("openrouter:google/gemini-2.5-flash"), _report())

    summary = build_run_summary(
        state, outcome="success", duration_s=1.0, catalog=_CATALOG
    )

    assert isinstance(summary.cost_usd, Decimal)
    assert summary.cost_usd > 0


def test_unknown_model_yields_no_cost(tmp_path: Path) -> None:
    state = tmp_path / "harness-state"
    _write(state, _usage("openrouter:google/gemini-3.1-flash-lite-preview"), _report())

    summary = build_run_summary(
        state, outcome="success", duration_s=1.0, catalog=_CATALOG
    )

    assert summary.cost_usd is None


def test_missing_artifacts_degrade_gracefully(tmp_path: Path) -> None:
    state = tmp_path / "harness-state"
    state.mkdir()

    summary = build_run_summary(
        state, outcome="failure", duration_s=0.0, catalog=_CATALOG
    )

    assert summary.model == ""
    assert summary.total_tokens == 0
    assert summary.conflicts_resolved == 0
    assert summary.cost_usd is None
    assert summary.outcome == "failure"


def test_emit_run_summary_logs_all_fields(tmp_path: Path) -> None:
    state = tmp_path / "harness-state"
    _write(state, _usage("openrouter:google/gemini-2.5-flash"), _report())
    summary = build_run_summary(
        state, outcome="success", duration_s=2.0, catalog=_CATALOG
    )

    captured: list[tuple[str, dict[str, object]]] = []

    class _Logger:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append((event, kwargs))

    emit_run_summary(_Logger(), summary)  # pyright: ignore[reportArgumentType]

    assert len(captured) == 1
    event, fields = captured[0]
    assert event == "run summary"
    assert fields["total_tokens"] == 1500
    assert fields["conflicts_resolved"] == 2
    assert isinstance(fields["cost_usd"], str)
    assert fields["outcome"] == "success"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
