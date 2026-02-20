from __future__ import annotations

import os
import re
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final


class OpenCodeEnvError(RuntimeError):
    """Raised when the OpenCode env file is invalid."""


DEFAULT_ENV_PATH: Final[Path] = Path("~/.config/forklift/opencode.env").expanduser()
SAFE_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._/-]+$")
REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "OPENCODE_VARIANT",
    "OPENCODE_AGENT",
    "OPENCODE_SERVER_PASSWORD",
)
OPTIONAL_PROVIDER_KEYS: Final[tuple[str, ...]] = (
    "OPENAI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)
DEFAULT_PORT: Final[int] = 4096
PRIMARY_API_KEYS: Final[tuple[str, ...]] = (
    "OPENCODE_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)


@dataclass(frozen=True)
class OpenCodeEnv:
    api_key: str | None
    model: str | None
    variant: str
    agent: str
    server_password: str
    server_port: int
    org: str | None = None
    timeout_seconds: int | None = None
    openai_api_key: str | None = None
    google_generative_ai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None

    def as_env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "OPENCODE_VARIANT": self.variant,
            "OPENCODE_AGENT": self.agent,
            "OPENCODE_SERVER_PASSWORD": self.server_password,
            "OPENCODE_SERVER_PORT": str(self.server_port),
        }
        if self.api_key:
            env["OPENCODE_API_KEY"] = self.api_key
        if self.model:
            env["OPENCODE_MODEL"] = self.model
        if self.timeout_seconds is not None:
            env["OPENCODE_TIMEOUT"] = str(self.timeout_seconds)
        if self.org:
            env["OPENCODE_ORG"] = self.org
        if self.openai_api_key:
            env["OPENAI_API_KEY"] = self.openai_api_key
        if self.google_generative_ai_api_key:
            env["GOOGLE_GENERATIVE_AI_API_KEY"] = self.google_generative_ai_api_key
        if self.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        if self.openrouter_api_key:
            env["OPENROUTER_API_KEY"] = self.openrouter_api_key
        return env


def load_opencode_env(path: Path | None = None) -> OpenCodeEnv:
    env_path = (path or DEFAULT_ENV_PATH).expanduser()
    if not env_path.exists():
        raise OpenCodeEnvError(f"Missing OpenCode env file at {env_path}")

    _validate_permissions(env_path)
    raw_text = _read_env_text(env_path)
    raw_values = _parse_env_text(raw_text, env_path)
    _ensure_required_keys(raw_values, env_path)

    sanitized = {key: value.strip() for key, value in raw_values.items()}

    model: str | None = None
    raw_model = sanitized.get("OPENCODE_MODEL")
    if raw_model:
        model = _require_safe_value(raw_model, "OPENCODE_MODEL", env_path)
    variant = _require_safe_value(
        sanitized["OPENCODE_VARIANT"], "OPENCODE_VARIANT", env_path
    )
    agent = _require_safe_value(sanitized["OPENCODE_AGENT"], "OPENCODE_AGENT", env_path)
    api_key = sanitized.get("OPENCODE_API_KEY")
    server_password = sanitized["OPENCODE_SERVER_PASSWORD"]
    if not server_password:
        raise OpenCodeEnvError(
            f"OPENCODE_SERVER_PASSWORD in {env_path} must not be empty"
        )

    primary_keys = [sanitized.get(key, "").strip() for key in PRIMARY_API_KEYS]
    if not any(primary_keys):
        raise OpenCodeEnvError(
            "At least one provider API key must be set (OpenCode, OpenAI, Gemini, Anthropic, or OpenRouter)"
        )

    timeout_val: int | None = None
    raw_timeout = sanitized.get("OPENCODE_TIMEOUT")
    if raw_timeout:
        try:
            timeout_val = int(raw_timeout)
        except ValueError as exc:  # pragma: no cover - defensive
            raise OpenCodeEnvError(
                f"OPENCODE_TIMEOUT in {env_path} must be an integer"
            ) from exc
        if timeout_val <= 0:
            raise OpenCodeEnvError(
                f"OPENCODE_TIMEOUT in {env_path} must be a positive integer"
            )

    raw_port = sanitized.get("OPENCODE_SERVER_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:  # pragma: no cover - defensive
        raise OpenCodeEnvError(
            f"OPENCODE_SERVER_PORT in {env_path} must be an integer"
        ) from exc
    if not (1 <= port <= 65535):
        raise OpenCodeEnvError(
            f"OPENCODE_SERVER_PORT in {env_path} must be between 1 and 65535"
        )

    org = sanitized.get("OPENCODE_ORG") or None
    provider_keys = {key: sanitized.get(key) or None for key in OPTIONAL_PROVIDER_KEYS}

    return OpenCodeEnv(
        api_key=api_key or None,
        model=model,
        variant=variant,
        agent=agent,
        server_password=server_password,
        server_port=port,
        org=org,
        timeout_seconds=timeout_val,
        openai_api_key=provider_keys["OPENAI_API_KEY"],
        google_generative_ai_api_key=provider_keys["GOOGLE_GENERATIVE_AI_API_KEY"],
        anthropic_api_key=provider_keys["ANTHROPIC_API_KEY"],
        openrouter_api_key=provider_keys["OPENROUTER_API_KEY"],
    )


def _parse_env_text(raw_text: str, env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for idx, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in raw_line:
            raise OpenCodeEnvError(
                f"Invalid line {idx} in {env_path}: expected KEY=VALUE format"
            )
        key, value = raw_line.split("=", 1)
        key = key.strip()
        value = value.rstrip()
        if not key:
            raise OpenCodeEnvError(
                f"Invalid line {idx} in {env_path}: missing key before '='"
            )
        values[key] = value
    return values


def _ensure_required_keys(values: dict[str, str], env_path: Path) -> None:
    missing = [
        key for key in REQUIRED_KEYS if key not in values or not values[key].strip()
    ]
    if missing:
        joined = ", ".join(missing)
        raise OpenCodeEnvError(f"Missing required keys in {env_path}: {joined}")


def _require_safe_value(value: str, key: str, env_path: Path) -> str:
    stripped = value.strip()
    if not SAFE_VALUE_PATTERN.fullmatch(stripped):
        raise OpenCodeEnvError(
            f"{key} in {env_path} contains invalid characters ({stripped!r}); "
            + "allowed characters are letters, digits, '.', '_', '-', and '/'"
        )
    return stripped


def _validate_permissions(env_path: Path) -> None:
    if os.name != "posix":  # pragma: no cover - permission checks only on POSIX
        return
    try:
        file_mode = stat.S_IMODE(env_path.stat().st_mode)
    except OSError as exc:  # pragma: no cover - best effort
        raise OpenCodeEnvError(f"Unable to stat {env_path}: {exc}") from exc
    if file_mode & 0o077:
        raise OpenCodeEnvError(
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
