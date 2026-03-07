from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Generic, Literal, NotRequired, TypedDict, TypeVar, cast

NodeShape = Literal["rectangle", "rounded", "circle", "rhombus", "hexagon", "stadium"]
ConnectionType = Literal["arrow", "line", "thick", "dotted", "dashed"]
FlowchartDirection = Literal["TD", "TB", "BT", "RL", "LR"]


class FlowchartDiagramType(TypedDict):
    type: Literal["flowchart"]
    direction: FlowchartDirection


class SequenceDiagramType(TypedDict):
    type: Literal["sequence"]
    participants: list[str]


class GanttDiagramType(TypedDict):
    type: Literal["gantt"]
    title: NotRequired[str]


class ClassDiagramType(TypedDict):
    type: Literal["classDiagram"]


class StateDiagramType(TypedDict):
    type: Literal["stateDiagram"]


DiagramType = (
    FlowchartDiagramType
    | SequenceDiagramType
    | GanttDiagramType
    | ClassDiagramType
    | StateDiagramType
)


class MermaidNode(TypedDict):
    id: str
    label: str
    shape: NodeShape
    metadata: NotRequired[dict[str, object]]


MermaidEdge = TypedDict(
    "MermaidEdge",
    {
        "from": str,
        "to": str,
        "type": ConnectionType,
        "label": NotRequired[str],
        "metadata": NotRequired[dict[str, object]],
    },
)


class MermaidAst(TypedDict):
    diagramType: DiagramType
    nodes: dict[str, MermaidNode]
    edges: list[MermaidEdge]
    metadata: dict[str, object]


class Token(TypedDict):
    type: Literal["node", "connection", "label", "identifier"]
    value: str
    position: int


class EdgeNodeData(TypedDict):
    id: str
    label: str
    shape: NodeShape


class EdgeParseResult(TypedDict):
    nodes: list[EdgeNodeData]
    edge: MermaidEdge | None


T = TypeVar("T")
E = TypeVar("E")


class OkResult(TypedDict, Generic[T]):
    success: Literal[True]
    data: T


class ErrResult(TypedDict, Generic[E]):
    success: Literal[False]
    error: E


Result = OkResult[T] | ErrResult[E]


FLOWCHART_HEADER_PATTERN = re.compile(r"^flowchart\s+(TD|TB|BT|RL|LR)$")
WHITESPACE_PATTERN = re.compile(r"^\s+")
IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*")
SHAPE_PATTERNS: Sequence[tuple[re.Pattern[str], NodeShape]] = (
    (re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\(\[([^\]]+)\]\)"), "stadium"),
    (re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\(\(([^)]+)\)\)"), "circle"),
    (re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\{\{([^}]+)\}\}"), "hexagon"),
    (re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\{([^}]+)\}"), "rhombus"),
    (re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\(([^)]+)\)"), "rounded"),
    (re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\[([^\]]+)\]"), "rectangle"),
)
CONNECTION_PATTERNS: Sequence[tuple[re.Pattern[str], ConnectionType]] = (
    (re.compile(r"^-\.->"), "dotted"),
    (re.compile(r"^==>"), "thick"),
    (re.compile(r"^---"), "dashed"),
    (re.compile(r"^-->"), "arrow"),
)


def ok(data: T) -> OkResult[T]:
    """Return a successful parse result payload."""

    return {"success": True, "data": data}


def err(error: E) -> ErrResult[E]:
    """Return a failed parse result payload."""

    return {"success": False, "error": error}


def create_node(
    id: str,
    label: str,
    shape: NodeShape = "rectangle",
    metadata: dict[str, object] | None = None,
) -> MermaidNode:
    """Create a Mermaid node record."""

    node: MermaidNode = {"id": id, "label": label, "shape": shape}
    if metadata is not None:
        node["metadata"] = metadata
    return node


def create_edge(
    from_node: str,
    to: str,
    connection_type: ConnectionType = "arrow",
    label: str | None = None,
    metadata: dict[str, object] | None = None,
) -> MermaidEdge:
    """Create a Mermaid edge record."""

    edge: MermaidEdge = {"from": from_node, "to": to, "type": connection_type}
    if label is not None:
        edge["label"] = label
    if metadata is not None:
        edge["metadata"] = metadata
    return edge


def create_ast(
    diagram_type: DiagramType,
    nodes: dict[str, MermaidNode] | None = None,
    edges: Sequence[MermaidEdge] | None = None,
    metadata: dict[str, object] | None = None,
) -> MermaidAst:
    """Create a Mermaid AST payload."""

    return {
        "diagramType": diagram_type,
        "nodes": dict(nodes) if nodes is not None else {},
        "edges": list(edges) if edges is not None else [],
        "metadata": dict(metadata) if metadata is not None else {},
    }


def add_node(ast: MermaidAst, node: MermaidNode) -> MermaidAst:
    """Return a copy of AST with the provided node added."""

    new_nodes = dict(ast["nodes"])
    new_nodes[node["id"]] = node
    return {
        **ast,
        "nodes": new_nodes,
    }


def add_edge(ast: MermaidAst, edge: MermaidEdge) -> MermaidAst:
    """Return a copy of AST with the provided edge appended."""

    return {
        **ast,
        "edges": [*ast["edges"], edge],
    }


def get_node_shape_symbols(shape: NodeShape) -> tuple[str, str]:
    """Return opening and closing symbols for a node shape."""

    match shape:
        case "rectangle":
            return ("[", "]")
        case "rounded":
            return ("(", ")")
        case "circle":
            return ("((", "))")
        case "rhombus":
            return ("{", "}")
        case "hexagon":
            return ("{{", "}}")
        case "stadium":
            return ("([", "])")


def tokenize_line(line: str) -> list[Token]:
    """Tokenize one Mermaid edge line using anchored, ordered token rules."""

    tokens: list[Token] = []
    position = 0

    while position < len(line):
        remaining = line[position:]

        ws_match = WHITESPACE_PATTERN.match(remaining)
        if ws_match is not None:
            position += len(ws_match.group(0))
            continue

        matched = False

        for pattern, shape in SHAPE_PATTERNS:
            shape_match = pattern.match(remaining)
            if shape_match is None:
                continue
            tokens.append(
                {
                    "type": "node",
                    "value": json.dumps(
                        {
                            "id": shape_match.group(1),
                            "label": shape_match.group(2),
                            "shape": shape,
                        }
                    ),
                    "position": position,
                }
            )
            position += len(shape_match.group(0))
            matched = True
            break

        if matched:
            continue

        for pattern, connection_type in CONNECTION_PATTERNS:
            connection_match = pattern.match(remaining)
            if connection_match is None:
                continue
            tokens.append(
                {
                    "type": "connection",
                    "value": connection_type,
                    "position": position,
                }
            )
            position += len(connection_match.group(0))
            matched = True
            break

        if matched:
            continue

        if remaining.startswith("|"):
            label_end = remaining.find("|", 1)
            if label_end > 0:
                tokens.append(
                    {
                        "type": "label",
                        "value": remaining[1:label_end],
                        "position": position,
                    }
                )
                position += label_end + 1
                continue

        identifier_match = IDENTIFIER_PATTERN.match(remaining)
        if identifier_match is not None:
            tokens.append(
                {
                    "type": "identifier",
                    "value": identifier_match.group(0),
                    "position": position,
                }
            )
            position += len(identifier_match.group(0))
            continue

        position += 1

    return tokens


def parse_edge_from_tokens(tokens: Sequence[Token]) -> EdgeParseResult | None:
    """Parse nodes and the primary edge definition from token stream."""

    if len(tokens) < 3:
        return None

    node_tokens: list[EdgeNodeData] = []
    connection_type: ConnectionType = "arrow"
    edge_label: str | None = None

    for token in tokens:
        token_type = token["type"]
        if token_type == "node":
            node_data = cast(EdgeNodeData, json.loads(token["value"]))
            node_tokens.append(node_data)
        elif token_type == "identifier":
            node_tokens.append(
                {
                    "id": token["value"],
                    "label": token["value"],
                    "shape": "rectangle",
                }
            )
        elif token_type == "connection":
            connection_type = cast(ConnectionType, token["value"])
        elif token_type == "label":
            edge_label = token["value"]

    if len(node_tokens) < 2:
        return None

    from_node = node_tokens[0]
    to_node = node_tokens[1]

    return {
        "nodes": node_tokens,
        "edge": create_edge(from_node["id"], to_node["id"], connection_type, edge_label),
    }


def parse_mermaid(input_text: str) -> Result[MermaidAst, str]:
    """Parse Mermaid flowchart text into a typed AST structure."""

    lines = input_text.strip().split("\n")
    if len(lines) == 0:
        return err("Empty input")

    header_line = lines[0].strip()
    header_match = FLOWCHART_HEADER_PATTERN.match(header_line)
    if header_match is None:
        return err("Invalid flowchart header")

    diagram_type: FlowchartDiagramType = {
        "type": "flowchart",
        "direction": cast(FlowchartDirection, header_match.group(1)),
    }

    ast = create_ast(diagram_type)
    processed_nodes: set[str] = set()

    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue

        tokens = tokenize_line(line)
        edge_result = parse_edge_from_tokens(tokens)
        if edge_result is None:
            continue

        for node_data in edge_result["nodes"]:
            if node_data["id"] in processed_nodes:
                continue
            ast = add_node(ast, create_node(node_data["id"], node_data["label"], node_data["shape"]))
            processed_nodes.add(node_data["id"])

        edge = edge_result["edge"]
        if edge is not None:
            ast = add_edge(ast, edge)

    return ok(ast)


__all__ = [
    "ClassDiagramType",
    "ConnectionType",
    "DiagramType",
    "EdgeNodeData",
    "EdgeParseResult",
    "ErrResult",
    "FlowchartDiagramType",
    "FlowchartDirection",
    "GanttDiagramType",
    "MermaidAst",
    "MermaidEdge",
    "MermaidNode",
    "NodeShape",
    "OkResult",
    "Result",
    "SequenceDiagramType",
    "StateDiagramType",
    "Token",
    "add_edge",
    "add_node",
    "create_ast",
    "create_edge",
    "create_node",
    "err",
    "get_node_shape_symbols",
    "ok",
    "parse_edge_from_tokens",
    "parse_mermaid",
    "tokenize_line",
]
