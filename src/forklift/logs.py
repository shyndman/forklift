from __future__ import annotations

import datetime

import structlog
from structlog.dev import Column, ConsoleRenderer, KeyValueColumnFormatter
from structlog.typing import EventDict, Processor


DIM = "\x1b[2m"
RESET = "\x1b[0m"
RUN_STYLE = "\x1b[38;2;129;161;193m"


LEVEL_STYLES: dict[str, tuple[str, str]] = {
    "debug": ("\x1b[38;2;144;140;170m", "dbug"),
    "info": ("\x1b[38;2;156;207;216m", "info"),
    "warning": ("\x1b[38;2;246;193;119m", "warn"),
    "error": ("\x1b[38;2;235;111;146m", "eror"),
    "exception": ("\x1b[38;2;235;111;146m", "exc!"),
    "critical": ("\x1b[48;2;235;111;146;38;2;33;32;46m", "crit"),
}


def timestamp_processor(
    _logger: structlog.stdlib.BoundLogger, _method: str, event_dict: EventDict
) -> EventDict:
    event_dict.setdefault("timestamp", datetime.datetime.now().strftime("%H:%M:%S"))
    return event_dict


def compact_level_processor(
    _logger: structlog.stdlib.BoundLogger, _method: str, event_dict: EventDict
) -> EventDict:
    level = str(event_dict.get("level", "")).lower()
    if level in LEVEL_STYLES:
        style, label = LEVEL_STYLES[level]
        event_dict["level"] = f"{style}[{label}]{RESET}"
    return event_dict


def ensure_run_processor(run_key: str, placeholder: str = "----") -> Processor:
    def processor(
        _logger: structlog.stdlib.BoundLogger, _method: str, event_dict: EventDict
    ) -> EventDict:
        value = str(event_dict.get(run_key, placeholder)).strip() or placeholder
        event_dict[run_key] = f"{RUN_STYLE}[{value}]{RESET}"
        return event_dict

    return processor


def extra_kv_processor(
    kv_key: str, default_key: str, reserved_keys: set[str]
) -> Processor:
    def processor(
        _logger: structlog.stdlib.BoundLogger, _method: str, event_dict: EventDict
    ) -> EventDict:
        extras: list[str] = []
        for key in sorted(list(event_dict.keys())):
            if key in reserved_keys or key.startswith("_"):
                continue
            extras.append(f"{key}={event_dict[key]}")
            event_dict.pop(key, None)
        event_dict[kv_key] = " ".join(extras)
        event_dict[default_key] = ""
        return event_dict

    return processor


def build_renderer(run_key: str = "run") -> tuple[list[Processor], ConsoleRenderer]:
    default_key = ""
    kv_key = "kv"
    extra_column = Column(
        run_key,
        KeyValueColumnFormatter(
            key_style=None,
            value_style="",
            reset_style=RESET,
            value_repr=str,
        ),
    )

    columns = [
        Column(
            "timestamp",
            KeyValueColumnFormatter(
                key_style=None,
                value_style=DIM,
                reset_style=RESET,
                value_repr=str,
                prefix="",
                postfix="",
            ),
        ),
        Column(
            "level",
            KeyValueColumnFormatter(
                key_style=None,
                value_style="",
                reset_style=RESET,
                value_repr=str,
            ),
        ),
        extra_column,
        Column(
            "event",
            KeyValueColumnFormatter(
                key_style=None,
                value_style="",
                reset_style=RESET,
                value_repr=str,
            ),
        ),
        Column(
            kv_key,
            KeyValueColumnFormatter(
                key_style=None,
                value_style="",
                reset_style=RESET,
                value_repr=str,
                prefix=" ",
            ),
        ),
        Column(
            default_key,
            KeyValueColumnFormatter(
                key_style=None,
                value_style="",
                reset_style=RESET,
                value_repr=str,
            ),
        ),
    ]

    renderer = ConsoleRenderer(colors=True, columns=columns)
    processors: list[Processor] = [
        timestamp_processor,
        compact_level_processor,
        ensure_run_processor(run_key),
        extra_kv_processor(
            kv_key,
            default_key,
            {"timestamp", "level", run_key, "event", kv_key, default_key},
        ),
    ]
    return processors, renderer
