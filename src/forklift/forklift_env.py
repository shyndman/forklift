"""Host-side loader for the operator's Forklift provider/model configuration.

Replaces the OpenCode-era ``opencode_env`` (config file ``opencode.env``). The
in-process Pydantic AI agent reads provider keys directly, so this loads
``~/.config/forklift/forklift.env`` into a :class:`ForkliftEnv` carrying the model
id, optional reasoning-effort knob, agent timeout, and provider API keys, and
emits them as the ``FORKLIFT_*`` / provider env forwarded into the sandbox.
"""

from __future__ import annotations

import os
import re
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_ENV_PATH: Final[Path] = Path("~/.config/forklift/forklift.env").expanduser()
# Safe characters for free-form values (effort level, provider key shapes).
SAFE_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._/-]+$")
# Model ids are ``provider:model`` (the colon is the only extra character).
MODEL_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._/:-]+$")
# Provider API keys read from the config and forwarded verbatim; pydantic-ai
# reads these directly. At least one must be present.
PROVIDER_KEYS: Final[tuple[str, ...]] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)


class ForkliftEnvError(RuntimeError):
    """Raised when the Forklift env file is missing or invalid."""


@dataclass(frozen=True)
class ForkliftEnv:
    """Operator model/provider configuration forwarded into the sandbox."""

    model: str | None
    effort: str | None
    timeout_seconds: int | None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    google_api_key: str | None = None
    gemini_api_key: str | None = None

    def as_env(self) -> dict[str, str]:
        """Render the subset of env vars forwarded into the sandbox container."""

        env: dict[str, str] = {}
        if self.model:
            env["FORKLIFT_MODEL"] = self.model
        if self.effort:
            env["FORKLIFT_MODEL_EFFORT"] = self.effort
        if self.timeout_seconds is not None:
            env["FORKLIFT_AGENT_TIMEOUT"] = str(self.timeout_seconds)
        if self.openai_api_key:
            env["OPENAI_API_KEY"] = self.openai_api_key
        if self.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        if self.openrouter_api_key:
            env["OPENROUTER_API_KEY"] = self.openrouter_api_key
        if self.google_api_key:
            env["GOOGLE_API_KEY"] = self.google_api_key
        if self.gemini_api_key:
            env["GEMINI_API_KEY"] = self.gemini_api_key
        return env


def load_forklift_env(path: Path | None = None) -> ForkliftEnv:
    """Load and validate the operator's Forklift env file."""

    env_path = (path or DEFAULT_ENV_PATH).expanduser()
    if not env_path.exists():
        raise ForkliftEnvError(f"Missing Forklift env file at {env_path}")

    _validate_permissions(env_path)
    raw_values = _parse_env_text(_read_env_text(env_path), env_path)
    sanitized = {key: value.strip() for key, value in raw_values.items()}

    model: str | None = None
    raw_model = sanitized.get("FORKLIFT_MODEL")
    if raw_model:
        model = _require_value(
            raw_model, "FORKLIFT_MODEL", env_path, MODEL_VALUE_PATTERN
        )

    effort: str | None = None
    raw_effort = sanitized.get("FORKLIFT_MODEL_EFFORT")
    if raw_effort:
        effort = _require_value(
            raw_effort, "FORKLIFT_MODEL_EFFORT", env_path, SAFE_VALUE_PATTERN
        )

    provider_values = {key: sanitized.get(key) or None for key in PROVIDER_KEYS}
    if not any(provider_values.values()):
        raise ForkliftEnvError(
            "At least one provider API key must be set in "
            + f"{env_path} (OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, "
            + "GOOGLE_API_KEY, or GEMINI_API_KEY)"
        )
    return ForkliftEnv(
        model=model,
        effort=effort,
        timeout_seconds=_parse_timeout(
            sanitized.get("FORKLIFT_AGENT_TIMEOUT"), env_path
        ),
        openai_api_key=provider_values["OPENAI_API_KEY"],
        anthropic_api_key=provider_values["ANTHROPIC_API_KEY"],
        openrouter_api_key=provider_values["OPENROUTER_API_KEY"],
        google_api_key=provider_values["GOOGLE_API_KEY"],
        gemini_api_key=provider_values["GEMINI_API_KEY"],
    )


def _parse_timeout(raw: str | None, env_path: Path) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ForkliftEnvError(
            f"FORKLIFT_AGENT_TIMEOUT in {env_path} must be an integer"
        ) from exc
    if value <= 0:
        raise ForkliftEnvError(
            f"FORKLIFT_AGENT_TIMEOUT in {env_path} must be a positive integer"
        )
    return value


def _parse_env_text(raw_text: str, env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for idx, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in raw_line:
            raise ForkliftEnvError(
                f"Invalid line {idx} in {env_path}: expected KEY=VALUE format"
            )
        key, value = raw_line.split("=", 1)
        key = key.strip()
        if not key:
            raise ForkliftEnvError(
                f"Invalid line {idx} in {env_path}: missing key before '='"
            )
        values[key] = value.rstrip()
    return values


def _require_value(
    value: str, key: str, env_path: Path, pattern: re.Pattern[str]
) -> str:
    stripped = value.strip()
    if not pattern.fullmatch(stripped):
        raise ForkliftEnvError(
            f"{key} in {env_path} contains invalid characters ({stripped!r})"
        )
    return stripped


def _validate_permissions(env_path: Path) -> None:
    if os.name != "posix":  # pragma: no cover - permission checks only on POSIX
        return
    try:
        file_mode = stat.S_IMODE(env_path.stat().st_mode)
    except OSError as exc:  # pragma: no cover - best effort
        raise ForkliftEnvError(f"Unable to stat {env_path}: {exc}") from exc
    if file_mode & 0o077:
        raise ForkliftEnvError(
            f"Insecure permissions on {env_path}: expected 0600-style, got {oct(file_mode)}"
        )


def _read_env_text(env_path: Path) -> str:
    mode = env_path.stat().st_mode
    if stat.S_ISFIFO(mode):
        with env_path.open("r", encoding="utf-8") as handle:
            return handle.read()
    if stat.S_ISSOCK(mode):
        data: list[str] = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(env_path))
            while True:
                chunk = client.recv(8192)
                if not chunk:
                    break
                data.append(chunk.decode("utf-8"))
        return "".join(data)
    return env_path.read_text()
