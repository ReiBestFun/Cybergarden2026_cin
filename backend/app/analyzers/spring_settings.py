"""Resolve literal settings within a Spring Boot configuration file, retaining source lines."""

import re
from typing import Iterator

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


Settings = dict[str, tuple[str, int]]
_SETTING_NAMES = {
    name.replace("-", ""): name for name in (
        "server.error.include-stacktrace", "spring.config.activate.on-profile",
        "spring.config.activate.on-cloud-platform",
    )
}


def _canonical(key: str) -> str:
    compact = key.replace("-", "").replace("_", "").lower()
    return _SETTING_NAMES.get(compact, key)


def _documents(path: str, content: str) -> Iterator[Settings]:
    if path.endswith(".properties"):
        values = {}
        for number, line in enumerate(content.splitlines(), 1):
            if line in {"#---", "!---"}:
                yield values
                values = {}
            elif match := re.fullmatch(r"\s*([\w.-]+)(?:\s*[:=]\s*|\s+)(.*?)\s*", line):
                key = _canonical(match[1])
                if key.startswith("management.") or key in _SETTING_NAMES.values():
                    values[key] = match[2], number
        yield values
        return

    def flatten(node, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, tuple[str, int]]]:
        if depth > 8:
            return
        if isinstance(node, MappingNode):
            for key, value in node.value:
                if isinstance(key, ScalarNode):
                    key_path = f"{prefix}.{key.value}" if prefix else key.value
                    if (key_path == "management" or key_path.startswith("management.")
                            or key_path in {"server", "server.error", "spring", "spring.config", "spring.config.activate"}
                            or _canonical(key_path) in _SETTING_NAMES.values()):
                        yield from flatten(value, key_path, depth + 1)
        elif isinstance(node, SequenceNode) and all(isinstance(item, ScalarNode) for item in node.value):
            yield prefix, (",".join(item.value for item in node.value), node.start_mark.line + 1)
        elif isinstance(node, ScalarNode):
            yield _canonical(prefix), (node.value, node.start_mark.line + 1)

    for document in yaml.compose_all(content, Loader=yaml.SafeLoader):
        yield dict(flatten(document))


def spring_settings(path: str, content: str) -> Iterator[Settings]:
    """Apply later overrides while keeping conditionally active documents separate."""
    defaults: Settings = {}
    conditional: dict[tuple[str, str], Settings] = {}
    for document in _documents(path, content):
        profile = document.get("spring.config.activate.on-profile", ("", 0))[0]
        cloud = document.get("spring.config.activate.on-cloud-platform", ("", 0))[0]
        if profile or cloud:
            activation = (profile, cloud)
            if activation not in conditional:
                conditional[activation] = defaults.copy()
            conditional[activation].update(document)
        else:
            defaults.update(document)
            for settings in conditional.values():
                settings.update(document)
    yield defaults
    yield from conditional.values()
