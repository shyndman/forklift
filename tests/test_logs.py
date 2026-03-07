from __future__ import annotations

import re
import unittest
from typing import cast

import structlog

from forklift.logs import LEVEL_MAPPING, RESET, compact_level_processor

ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


class CompactLevelProcessorTests(unittest.TestCase):
    def test_compact_level_processor_formats_mapped_levels(self) -> None:
        logger = cast(structlog.stdlib.BoundLogger, object())
        for level, style in LEVEL_MAPPING.items():
            event_dict: dict[str, object] = {"level": level}

            _ = compact_level_processor(logger, "info", event_dict)

            self.assertEqual(
                event_dict["level"],
                f"{style['bracket']}[{style['text']}{style['bracket']}]{RESET}",
            )

    def test_compact_level_processor_preserves_unknown_levels(self) -> None:
        logger = cast(structlog.stdlib.BoundLogger, object())
        event_dict: dict[str, object] = {"level": "trace"}

        _ = compact_level_processor(logger, "info", event_dict)

        self.assertEqual(event_dict["level"], "trace")

    def test_level_mapping_labels_are_fixed_width(self) -> None:
        for style in LEVEL_MAPPING.values():
            label = ANSI_ESCAPE_PATTERN.sub("", style["text"])
            self.assertEqual(len(label), 4)

    def test_critical_brackets_match_error_exception_brackets(self) -> None:
        error_bracket = LEVEL_MAPPING["error"]["bracket"]

        self.assertEqual(LEVEL_MAPPING["exception"]["bracket"], error_bracket)
        self.assertEqual(LEVEL_MAPPING["critical"]["bracket"], error_bracket)
