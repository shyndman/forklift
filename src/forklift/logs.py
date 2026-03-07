from __future__ import annotations

import datetime
from typing import TypedDict, cast

import structlog
from structlog.dev import Column, ConsoleRenderer, KeyValueColumnFormatter
from structlog.typing import EventDict, Processor


DIM = "\x1b[2m"
RESET = "\x1b[0m"
RUN_STYLE = "\x1b[38;2;129;161;193m"


class LevelStyle(TypedDict):
    text: str
    bracket: str


def hex_to_ansi_fg(value: int) -> str:
    """Convert a 24-bit RGB hex color to an ANSI foreground escape sequence."""

    red = (value >> 16) & 0xFF
    green = (value >> 8) & 0xFF
    blue = value & 0xFF
    return f"\x1b[38;2;{red};{green};{blue}m"


LEVEL_MAPPING: dict[str, LevelStyle] = {
    "debug": {
        "text": f"{hex_to_ansi_fg(0x908CAA)}dbug{RESET}",
        "bracket": hex_to_ansi_fg(0x817E99),
    },
    "info": {
        "text": f"{hex_to_ansi_fg(0x9CCFD8)}info{RESET}",
        "bracket": hex_to_ansi_fg(0x8CBAC2),
    },
    "warning": {
        "text": f"{hex_to_ansi_fg(0xF6C177)}warn{RESET}",
        "bracket": hex_to_ansi_fg(0xDDAD6B),
    },
    "error": {
        "text": f"{hex_to_ansi_fg(0xEB6F92)}eror{RESET}",
        "bracket": hex_to_ansi_fg(0xD46483),
    },
    "exception": {
        "text": f"{hex_to_ansi_fg(0xEB6F92)}exc!{RESET}",
        "bracket": hex_to_ansi_fg(0xD46483),
    },
    "critical": {
        "text": "\x1b[48;2;235;111;146;38;2;33;32;46mcrit\x1b[0m",
        "bracket": hex_to_ansi_fg(0xD46483),
    },
}


def timestamp_processor(
    _logger: structlog.stdlib.BoundLogger, _method: str, event_dict: EventDict
) -> EventDict:
    event_dict.setdefault("timestamp", datetime.datetime.now().strftime("%H:%M:%S"))
    return event_dict


def compact_level_processor(
    _logger: structlog.stdlib.BoundLogger, _method: str, event_dict: EventDict
) -> EventDict:
    level_raw = cast(object, event_dict.get("level", ""))
    level = str(level_raw).lower()
    style = LEVEL_MAPPING.get(level)
    if style is not None:
        bracket = style["bracket"]
        text = style["text"]
        event_dict["level"] = f"{bracket}[{text}{bracket}]{RESET}"
    return event_dict


def ensure_run_processor(run_key: str, placeholder: str = "----") -> Processor:
    def processor(
        _logger: structlog.stdlib.BoundLogger, _method: str, event_dict: EventDict
    ) -> EventDict:
        value_raw = cast(object, event_dict.get(run_key, placeholder))
        value = str(value_raw).strip() or placeholder
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
