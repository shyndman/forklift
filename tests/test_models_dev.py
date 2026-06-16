"""Tests for the models.dev pricing source (`forklift.models_dev`).

Covers the live-fetch / cache-fallback lifecycle (no TTL: newest successful fetch
wins, stale cache serves when offline) and the keyed cost lookup, including the
misses that must price to ``None`` -- unknown providers, absent models, and
malformed model strings.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from forklift.models_dev import Catalog, ModelCost, load_catalog, price_tokens

_MODEL = "openrouter:google/gemini-3-flash-preview"
_HTTPX_GET = "forklift.models_dev.httpx.get"


def _catalog(cost: ModelCost) -> Catalog:
    return {"openrouter": {"models": {"google/gemini-3-flash-preview": {"cost": cost}}}}


class _FakeResponse:
    def __init__(self, payload: Catalog) -> None:
        self._payload: Catalog = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Catalog:
        return self._payload


def _responder(payload: Catalog) -> Callable[[str, float], _FakeResponse]:
    def _get(url: str, timeout: float) -> _FakeResponse:
        del url, timeout
        return _FakeResponse(payload)

    return _get


def _failer() -> Callable[[str, float], _FakeResponse]:
    def _get(url: str, timeout: float) -> _FakeResponse:
        del url, timeout
        raise httpx.ConnectError("offline")

    return _get


def test_price_tokens_sums_all_components() -> None:
    catalog = _catalog(
        {
            "input": 0.5,
            "output": 3,
            "cache_read": 0.05,
            "cache_write": 0.083333,
            "reasoning": 3,
        }
    )

    price = price_tokens(
        catalog,
        _MODEL,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        cache_write_tokens=100,
    )

    # (1000*0.5 + 500*3 + 200*0.05 + 100*0.083333) / 1e6, reasoning ignored.
    assert price == Decimal("0.0020183333")


def test_price_tokens_ignores_reasoning_rate() -> None:
    # Reasoning tokens are output tokens; the reasoning rate must not be applied.
    catalog = _catalog({"output": 2, "reasoning": 10})

    assert price_tokens(catalog, _MODEL, output_tokens=100) == Decimal("0.0002")


def test_price_tokens_prices_only_present_components() -> None:
    catalog = _catalog({"input": 1.0})

    price = price_tokens(catalog, _MODEL, input_tokens=10, output_tokens=999)

    assert price == Decimal("0.00001")


def test_unknown_provider_yields_none() -> None:
    catalog = _catalog({"input": 1.0})

    assert price_tokens(catalog, "ollama:qwen3.6-35B-A3B", input_tokens=10) is None


def test_known_provider_unknown_model_yields_none() -> None:
    catalog = _catalog({"input": 1.0})

    assert price_tokens(catalog, "openrouter:no/such-model", input_tokens=10) is None


def test_malformed_model_string_yields_none() -> None:
    catalog = _catalog({"input": 1.0})

    assert price_tokens(catalog, "no-colon-here", input_tokens=10) is None


def test_load_catalog_fetches_and_caches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    payload = _catalog({"input": 0.5})
    monkeypatch.setattr(_HTTPX_GET, _responder(payload))

    result = load_catalog()

    assert result == payload
    cache_file = tmp_path / "forklift" / "models-dev-api.json"
    assert json.loads(cache_file.read_text(encoding="utf-8")) == payload


def test_load_catalog_overwrites_stale_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache_file = tmp_path / "forklift" / "models-dev-api.json"
    cache_file.parent.mkdir(parents=True)
    _ = cache_file.write_text(json.dumps(_catalog({"input": 9.9})), encoding="utf-8")
    fresh = _catalog({"input": 0.5})
    monkeypatch.setattr(_HTTPX_GET, _responder(fresh))

    assert load_catalog() == fresh
    assert json.loads(cache_file.read_text(encoding="utf-8")) == fresh


def test_load_catalog_falls_back_to_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache_file = tmp_path / "forklift" / "models-dev-api.json"
    cache_file.parent.mkdir(parents=True)
    cached = _catalog({"input": 0.5})
    _ = cache_file.write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(_HTTPX_GET, _failer())

    assert load_catalog() == cached


def test_load_catalog_returns_empty_without_network_or_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(_HTTPX_GET, _failer())

    assert load_catalog() == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
