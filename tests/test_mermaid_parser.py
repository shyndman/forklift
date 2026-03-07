from __future__ import annotations

import unittest
from typing import cast

from forklift.mermaid import (
    ErrResult,
    MermaidAst,
    OkResult,
    Result,
    parse_edge_from_tokens,
    parse_mermaid,
    tokenize_line,
)


class MermaidParserTests(unittest.TestCase):
    def _expect_success(self, result: Result[MermaidAst, str]) -> MermaidAst:
        self.assertTrue(result["success"])
        if not result["success"]:
            failure = cast(ErrResult[str], result)
            self.fail(f"Expected parse success, got error: {failure['error']}")
        success = cast(OkResult[MermaidAst], result)
        return success["data"]

    def _expect_failure(self, result: Result[MermaidAst, str]) -> str:
        self.assertFalse(result["success"])
        if result["success"]:
            self.fail("Expected parse failure")
        failure = cast(ErrResult[str], result)
        return failure["error"]

    def test_parses_valid_flowchart_with_shapes_and_label(self) -> None:
        result = parse_mermaid(
            """
            flowchart TD
              A[Start] --> B{Decision}
              B -->|Yes| C([Ship It])
            """
        )

        ast = self._expect_success(result)
        self.assertEqual(ast["diagramType"], {"type": "flowchart", "direction": "TD"})
        self.assertEqual(set(ast["nodes"].keys()), {"A", "B", "C"})
        self.assertEqual(ast["nodes"]["A"]["shape"], "rectangle")
        self.assertEqual(ast["nodes"]["B"]["shape"], "rhombus")
        self.assertEqual(ast["nodes"]["C"]["shape"], "stadium")

        self.assertEqual(len(ast["edges"]), 2)
        self.assertEqual(ast["edges"][0], {"from": "A", "to": "B", "type": "arrow"})
        self.assertEqual(
            ast["edges"][1],
            {"from": "B", "to": "C", "type": "arrow", "label": "Yes"},
        )

    def test_rejects_invalid_flowchart_header(self) -> None:
        result = parse_mermaid("graph TD\nA --> B")
        self.assertEqual(self._expect_failure(result), "Invalid flowchart header")

    def test_whitespace_only_input_returns_invalid_header(self) -> None:
        result = parse_mermaid("   \n\t")
        self.assertEqual(self._expect_failure(result), "Invalid flowchart header")

    def test_deduplicates_nodes_by_identifier(self) -> None:
        result = parse_mermaid(
            """
            flowchart LR
              A --> B
              A --> C
            """
        )

        ast = self._expect_success(result)
        self.assertEqual(set(ast["nodes"].keys()), {"A", "B", "C"})
        self.assertEqual(len(ast["nodes"]), 3)
        self.assertEqual(len(ast["edges"]), 2)

    def test_unmatched_label_falls_back_to_identifier_nodes(self) -> None:
        result = parse_mermaid(
            """
            flowchart TD
              A --> |Yes B
            """
        )

        ast = self._expect_success(result)
        self.assertEqual(set(ast["nodes"].keys()), {"A", "Yes", "B"})
        self.assertEqual(ast["edges"][0], {"from": "A", "to": "Yes", "type": "arrow"})

    def test_last_connection_token_wins(self) -> None:
        tokens = tokenize_line("A --> B --- C")
        parsed = parse_edge_from_tokens(tokens)

        self.assertIsNotNone(parsed)
        if parsed is None:
            self.fail("Expected edge parse result")

        self.assertEqual(parsed["edge"], {"from": "A", "to": "B", "type": "dashed"})


if __name__ == "__main__":
    _ = unittest.main()
