"""Tests for the host-side Forklift env loader (`forklift.forklift_env`).

Covers the renamed file/keys (FORKLIFT_MODEL / FORKLIFT_MODEL_EFFORT /
FORKLIFT_AGENT_TIMEOUT), the dropped OPENCODE_* keys, provider-key requirements
(including Gemini via GOOGLE_API_KEY), and the env rendered into the sandbox.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from forklift.forklift_env import (
    ForkliftEnv,
    ForkliftEnvError,
    load_forklift_env,
)


def _write_env(tmp_path: Path, body: str) -> Path:
    env_path = tmp_path / "forklift.env"
    _ = env_path.write_text(body, encoding="utf-8")
    env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 (loader rejects group/other)
    return env_path


def test_loads_model_effort_timeout_and_provider_key(tmp_path: Path) -> None:
    env_path = _write_env(
        tmp_path,
        "FORKLIFT_MODEL=openrouter:google/gemini-2.5-flash\nFORKLIFT_MODEL_EFFORT=high\nFORKLIFT_AGENT_TIMEOUT=900\nOPENROUTER_API_KEY=sk-or-test\n",
    )

    env = load_forklift_env(env_path)

    assert env.model == "openrouter:google/gemini-2.5-flash"
    assert env.effort == "high"
    assert env.timeout_seconds == 900
    assert env.openrouter_api_key == "sk-or-test"


def test_gemini_via_google_api_key(tmp_path: Path) -> None:
    env_path = _write_env(
        tmp_path,
        "FORKLIFT_MODEL=google:gemini-2.5-flash\nGOOGLE_API_KEY=g-test\n",
    )

    env = load_forklift_env(env_path)

    assert env.google_api_key == "g-test"
    assert env.as_env()["GOOGLE_API_KEY"] == "g-test"
    assert env.as_env()["FORKLIFT_MODEL"] == "google:gemini-2.5-flash"


def test_gemini_api_key_satisfies_provider_requirement(tmp_path: Path) -> None:
    env_path = _write_env(tmp_path, "GEMINI_API_KEY=gem-test\n")

    env = load_forklift_env(env_path)

    assert env.gemini_api_key == "gem-test"
    assert env.model is None


def test_dropped_opencode_keys_are_ignored(tmp_path: Path) -> None:
    env_path = _write_env(
        tmp_path,
        "OPENCODE_VARIANT=default\nOPENCODE_AGENT=worker\nOPENCODE_SERVER_PASSWORD=pw\nOPENAI_API_KEY=sk-test\n",
    )

    env = load_forklift_env(env_path)

    rendered = env.as_env()
    assert env.openai_api_key == "sk-test"
    assert not any(key.startswith("OPENCODE_") for key in rendered)


def test_missing_provider_key_fails(tmp_path: Path) -> None:
    env_path = _write_env(tmp_path, "FORKLIFT_MODEL=openrouter:x/y\n")

    with pytest.raises(ForkliftEnvError, match="provider API key"):
        _ = load_forklift_env(env_path)


def test_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ForkliftEnvError, match="Missing"):
        _ = load_forklift_env(tmp_path / "absent.env")


def test_non_integer_timeout_fails(tmp_path: Path) -> None:
    env_path = _write_env(tmp_path, "OPENAI_API_KEY=sk\nFORKLIFT_AGENT_TIMEOUT=soon\n")

    with pytest.raises(ForkliftEnvError, match="integer"):
        _ = load_forklift_env(env_path)


def test_insecure_permissions_fail(tmp_path: Path) -> None:
    env_path = tmp_path / "forklift.env"
    _ = env_path.write_text("OPENAI_API_KEY=sk\n", encoding="utf-8")
    env_path.chmod(0o644)
    if os.name != "posix":
        pytest.skip("permission check is POSIX-only")

    with pytest.raises(ForkliftEnvError, match="permissions"):
        _ = load_forklift_env(env_path)


def test_as_env_omits_unset_fields() -> None:
    env = ForkliftEnv(model=None, effort=None, timeout_seconds=None, openai_api_key="k")

    rendered = env.as_env()

    assert rendered == {"OPENAI_API_KEY": "k"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
