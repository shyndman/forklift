"""Model pricing sourced from models.dev (https://models.dev/api.json).

The host prices each run from the models.dev catalog: a live fetch on every run,
written through to an XDG cache so a later network failure can fall back to the
most recent successful download. There is no TTL -- the newest successful fetch
wins, and a stale cache serves indefinitely when the network is unavailable.

Lookup is a direct keyed walk: ``catalog[provider]["models"][model_id]["cost"]``
where ``model`` is the ``provider:model_id`` string the harness records. Anything
the catalog does not cover -- unknown providers (e.g. local ``ollama`` models),
deprecated provider aliases (e.g. ``google-gla``), or absent model ids -- prices
to ``None`` so the rest of the run summary is unaffected. Costs are quoted per
one million tokens.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import TypedDict, cast

import httpx

CATALOG_URL = "https://models.dev/api.json"
CACHE_FILE_NAME = "models-dev-api.json"
_FETCH_TIMEOUT_S = 10.0
_TOKENS_PER_UNIT = Decimal(1_000_000)


class ModelCost(TypedDict, total=False):
    """Per-million-token rates for a single served model (only fields we price)."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    reasoning: float


class ModelEntry(TypedDict, total=False):
    """A served model within a provider; ``cost`` is absent for unpriced models."""

    cost: ModelCost


class CatalogProvider(TypedDict, total=False):
    """A provider entry in the catalog, keyed by AI-SDK model id under ``models``."""

    models: dict[str, ModelEntry]


# Top-level catalog: provider id -> provider. Untrusted network JSON, so every
# nested access is isinstance-guarded before the structure is trusted.
Catalog = dict[str, CatalogProvider]

# models.dev ``cost`` keys paired with the usage token-count they price. Reasoning
# tokens are output tokens, so the ``reasoning`` rate is intentionally ignored.
_COST_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cache_read", "cache_read_tokens"),
    ("cache_write", "cache_write_tokens"),
)


def load_catalog() -> Catalog:
    """Return the models.dev catalog: live fetch, falling back to the cached copy.

    A successful fetch is written through to the XDG cache. Returns an empty dict
    when neither the network nor a cached copy yields a usable catalog.
    """

    fetched = _fetch_catalog()
    if fetched is not None:
        _store_cache(fetched)
        return fetched
    return _read_cache()


def price_tokens(
    catalog: Catalog,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal | None:
    """Price aggregated token counts for ``model`` against ``catalog``.

    Returns ``None`` when the model is not represented in the catalog; otherwise
    the summed cost in USD across the priced token components.
    """

    cost = _lookup_cost(catalog, model)
    if cost is None:
        return None
    counts = {
        "input": input_tokens,
        "output": output_tokens,
        "cache_read": cache_read_tokens,
        "cache_write": cache_write_tokens,
    }
    total = Decimal(0)
    for cost_key, _token_field in _COST_COMPONENTS:
        rate = cost.get(cost_key)
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            continue
        total += Decimal(str(rate)) * counts[cost_key]
    return total / _TOKENS_PER_UNIT


def _lookup_cost(catalog: Catalog, model: str) -> ModelCost | None:
    """Resolve the ``cost`` mapping for a ``provider:model_id`` string, or ``None``."""

    provider_id, separator, model_id = model.partition(":")
    if not separator or not provider_id or not model_id:
        return None
    provider = catalog.get(provider_id)
    if not isinstance(provider, dict):
        return None
    models = provider.get("models")
    if not isinstance(models, dict):
        return None
    entry = models.get(model_id)
    if not isinstance(entry, dict):
        return None
    cost = entry.get("cost")
    return cost if isinstance(cost, dict) else None


def _fetch_catalog() -> Catalog | None:
    """GET the live catalog; return ``None`` on any network/HTTP/parse failure."""

    try:
        response = httpx.get(CATALOG_URL, timeout=_FETCH_TIMEOUT_S)
        _ = response.raise_for_status()
        parsed: object = cast(object, response.json())
    except (httpx.HTTPError, json.JSONDecodeError):
        return None
    return cast(Catalog, parsed) if isinstance(parsed, dict) else None


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "forklift" / CACHE_FILE_NAME


def _store_cache(catalog: Catalog) -> None:
    """Persist the catalog atomically; cache failures are non-fatal."""

    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f"{path.name}.tmp"
        _ = tmp.write_text(json.dumps(catalog), encoding="utf-8")
        _ = tmp.replace(path)
    except OSError:
        return


def _read_cache() -> Catalog:
    try:
        parsed: object = cast(
            object, json.loads(_cache_path().read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return cast(Catalog, parsed) if isinstance(parsed, dict) else {}
