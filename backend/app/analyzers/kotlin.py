"""Common syntax queries for Kotlin source and Gradle Kotlin DSL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from tree_sitter import Language, Node, Parser
from tree_sitter_kotlin import language

KOTLIN = Language(language())
STRING_TYPES = {"string_literal", "multiline_string_literal"}
TEMPLATE = re.compile(r"\\.|[$](?:\{(?P<braced>[^{}]+)\}|(?P<name>[A-Za-z_]\w*))")
_SCOPES = {"block", "class_body", "source_file", "lambda_literal"}
_COMMENTS = {"line_comment", "block_comment"}


def text(node: Node) -> str:
    return node.text.decode("utf-8")


def children(node: Node) -> list[Node]:
    return [child for child in node.named_children if child.type not in _COMMENTS]


def child(node: Node, kind: str) -> Node | None:
    return next((item for item in children(node) if item.type == kind), None)


def walk(node: Node, kind: str, *, stop: frozenset[str] = frozenset()) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == kind:
            yield current
        if current.type not in stop:
            stack.extend(reversed(children(current)))


def line(node: Node) -> int:
    return node.start_point.row + 1


def end_line(node: Node) -> int:
    return max(line(node), node.end_point.row + bool(node.end_point.column))


def string_content(node: Node) -> str | None:
    if node.type not in STRING_TYPES:
        return None
    value = text(node)
    width = 3 if value.startswith('"""') else 1
    return value[width:-width]


def literal(node: Node) -> str | None:
    value = string_content(node)
    if value is not None and not any(match.lastgroup for match in TEMPLATE.finditer(value)):
        return value
    return None


def operator(node: Node) -> str:
    token = node.child_by_field_name("operator")
    if token is not None:
        return text(token)
    return next((item.type for item in node.children if item.type in {"+", "?:", "="}), "")


def property_value(node: Node) -> tuple[str, Node] | None:
    declaration = child(node, "variable_declaration")
    parts = children(node)
    if declaration is None or not any(item.type == "=" for item in node.children):
        return None
    name = child(declaration, "identifier")
    index = parts.index(declaration) + 1
    if index == len(parts):
        return None
    initializer = parts[index]
    return (text(name), initializer) if name is not None else None


@dataclass(frozen=True)
class Call:
    name: str
    receiver: Node | None
    args: tuple[Node, ...]
    block: Node | None


def call(node: Node) -> Call | None:
    if node.type != "call_expression":
        return None
    parts = children(node)
    if not parts:
        return None
    target = parts[0]
    receiver = None
    if target.type == "navigation_expression":
        names = children(target)
        if len(names) != 2 or names[-1].type != "identifier":
            return None
        receiver, target = names
    if target.type != "identifier":
        return None
    arguments = child(node, "value_arguments")
    args = []
    if arguments is not None:
        for argument in children(arguments):
            values = children(argument)
            if values:
                args.append(values[-1])
    body = child(node, "annotated_lambda")
    return Call(text(target), receiver, tuple(args), child(body, "lambda_literal") if body else None)


class KotlinFile:
    def __init__(self, source: str):
        self.root = Parser(KOTLIN).parse(source.encode("utf-8")).root_node
        self.bindings: dict[Node, dict[str, list[Node]]] = {}
        for entry in walk(self.root, "property_declaration"):
            value = property_value(entry)
            if value is not None and any(part.type == "val" for part in entry.children):
                scope = entry.parent
                while scope is not None and scope.type not in _SCOPES:
                    scope = scope.parent
                if scope is not None:
                    self.bindings.setdefault(scope, {}).setdefault(value[0], []).append(value[1])

    def binding(self, name: str, use: Node) -> Node | None:
        scope = use.parent
        while scope is not None:
            values = self.bindings.get(scope, {}).get(name, ())
            for value in reversed(values):
                if value.end_byte <= use.start_byte or scope.type in {"class_body", "source_file"}:
                    return value
            if scope.type == "function_declaration":
                params = child(scope, "function_value_parameters")
                if params and any(
                    text(identifier) == name
                    for param in children(params) if param.type == "parameter"
                    if (identifier := child(param, "identifier")) is not None
                ):
                    return None
            scope = scope.parent
        return None

    def resolve(self, node: Node) -> Node:
        seen: set[Node] = set()
        while node not in seen:
            seen.add(node)
            if node.type == "identifier":
                target = self.binding(text(node), node)
            elif node.type == "parenthesized_expression":
                target = next(iter(children(node)), None)
            else:
                break
            if target is None:
                break
            node = target
        return node
