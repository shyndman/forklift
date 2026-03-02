from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import structlog
from structlog.stdlib import BoundLogger
from typing import cast

from .opencode_env import OpenCodeEnv, SAFE_VALUE_PATTERN

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))


def build_container_env(
    env: OpenCodeEnv,
    main_branch: str,
    run_id: str,
    *,
    forward_tz: bool,
) -> dict[str, str]:
    """Build environment payload forwarded into the sandbox container."""

    container_env = dict(env.as_env())
    container_env["FORKLIFT_MAIN_BRANCH"] = main_branch
    container_env["FORKLIFT_RUN_ID"] = run_id
    tz_value = host_timezone_value(forward_tz=forward_tz)
    if tz_value is not None:
        container_env["TZ"] = tz_value
    return container_env


def host_timezone_value(*, forward_tz: bool) -> str | None:
    """Return a safe host `TZ` value when timezone forwarding is enabled."""

    if not forward_tz:
        return None
    tz_value = os.environ.get("TZ")
    if not tz_value:
        logger.warning("--forward-tz enabled but host TZ is unset; skipping TZ forwarding.")
        return None
    if contains_control_characters(tz_value):
        logger.warning(
            "Host TZ value contains control characters; skipping TZ forwarding.",
            value=tz_value,
        )
        return None
    logger.info("Forwarding host timezone", timezone=tz_value)
    return tz_value


def contains_control_characters(value: str) -> bool:
    """Return whether a string contains ASCII control characters."""

    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def apply_cli_overrides(
    env: OpenCodeEnv,
    *,
    model: str | None,
    variant: str | None,
    agent: str | None,
) -> OpenCodeEnv:
    """Apply CLI override flags for OpenCode model/variant/agent settings."""

    resolved_model = validated_override(model, env.model, "model")
    resolved_variant = validated_override(variant, env.variant, "variant")
    resolved_agent = validated_override(agent, env.agent, "agent")
    return replace(env, model=resolved_model, variant=resolved_variant, agent=resolved_agent)


def validated_override(
    override: str | None,
    current: str | None,
    label: str,
) -> str | None:
    """Validate a single CLI override against the safe-value pattern."""

    if override is None:
        return current
    if not SAFE_VALUE_PATTERN.fullmatch(override):
        logger.error(
            "Invalid %s value %r; expected pattern %s",
            label,
            override,
            SAFE_VALUE_PATTERN.pattern,
        )
        raise SystemExit(1)
    return override


def resolved_main_branch(main_branch: str | None) -> str:
    """Normalize and validate the selected main branch for orchestration."""

    branch = (main_branch or "main").strip()
    if not branch:
        logger.error("--main-branch value must not be empty")
        raise SystemExit(1)
    if not SAFE_VALUE_PATTERN.fullmatch(branch):
        logger.error(
            "Invalid --main-branch value %r; expected pattern %s",
            branch,
            SAFE_VALUE_PATTERN.pattern,
        )
        raise SystemExit(1)
    return branch


def resolve_chown_target(spec: str | None) -> tuple[int, int]:
    """Parse `--chown` into UID/GID with host defaults when omitted."""

    default_uid, default_gid = default_host_ids()
    normalized = (spec or "").strip()
    if not normalized:
        return default_uid, default_gid

    uid_part, _, gid_part = normalized.partition(":")
    uid_part = uid_part.strip()
    gid_part = gid_part.strip()
    if not uid_part:
        logger.error("Invalid --chown value %r; UID is required.", normalized)
        raise SystemExit(1)

    uid = parse_id_component(uid_part, "UID")
    gid = default_gid if gid_part == "" else parse_id_component(gid_part, "GID")
    return uid, gid


def default_host_ids() -> tuple[int, int]:
    """Return host UID/GID defaults, falling back to 1000 on unsupported platforms."""

    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    return uid, gid


def parse_id_component(raw: str, label: str) -> int:
    """Parse one UID/GID component and enforce non-negative integer values."""

    try:
        value = int(raw, 10)
    except ValueError:
        logger.exception("Invalid %s %r in --chown value; expected integer.", label, raw)
        raise SystemExit(1) from None
    if value < 0:
        logger.error(
            "Invalid %s %s in --chown value; expected non-negative integer.",
            label,
            value,
        )
        raise SystemExit(1)
    return value


def chown_artifact(target: Path, *, label: str, uid: int, gid: int) -> None:
    """Recursively reset ownership on harness artifacts after container execution."""

    if not target.exists():
        logger.debug("%s directory %s missing; skipping ownership reset.", label, target)
        return

    logger.info("Reset artifact ownership", label=label, uid=uid, gid=gid)
    try:
        chown_path_recursive(target, uid=uid, gid=gid)
    except PermissionError as exc:
        logger.warning(
            "Unable to chown artifact",
            label=label,
            uid=uid,
            gid=gid,
            error=exc,
        )
    except OSError as exc:
        logger.warning(
            "Failed to chown artifact",
            label=label,
            uid=uid,
            gid=gid,
            error=exc,
        )


def chown_path_recursive(path: Path, *, uid: int, gid: int) -> None:
    """Apply ownership recursively without following symlinks."""

    set_owner(path, uid=uid, gid=gid)
    if path.is_symlink():
        return
    try:
        is_dir = path.is_dir()
    except OSError:
        return
    if not is_dir:
        return

    try:
        children = list(path.iterdir())
    except OSError:
        return

    for child in children:
        try:
            chown_path_recursive(child, uid=uid, gid=gid)
        except FileNotFoundError:
            continue


def set_owner(path: Path, *, uid: int, gid: int) -> None:
    """Set ownership on one filesystem path with a non-following symlink policy."""

    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except NotImplementedError:
        os.chown(path, uid, gid)
