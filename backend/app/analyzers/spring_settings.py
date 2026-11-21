"""Read literal Spring Boot settings with their source lines, one document at a time."""

import re
from typing import Iterator

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


def spring_settings(path: str, content: str) -> Iterator[dict[str, tuple[str, int]]]:
    if path.endswith(".properties"):
        values = {}
        for number, line in enumerate(content.splitlines(), 1):
            if match := re.fullmatch(r"\s*([\w.-]+)\s*[:=]\s*(.*?)\s*", line):
                values[match[1]] = match[2], number
        yield values
        return

    def flatten(node, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, tuple[str, int]]]:
        if depth > 8:
            return
        if isinstance(node, MappingNode):
            for key, value in node.value:
                if isinstance(key, ScalarNode):
                    key_path = f"{prefix}.{key.value}" if prefix else key.value
                    if key_path.split(".", 1)[0] in {"management", "server"}:
                        yield from flatten(value, key_path, depth + 1)
        elif isinstance(node, SequenceNode) and all(isinstance(item, ScalarNode) for item in node.value):
            yield prefix, (",".join(item.value for item in node.value), node.start_mark.line + 1)
        elif isinstance(node, ScalarNode):
            yield prefix, (node.value, node.start_mark.line + 1)

    for document in yaml.compose_all(content, Loader=yaml.SafeLoader):
        yield dict(flatten(document))
