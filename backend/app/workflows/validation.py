import re
from collections import Counter, defaultdict, deque
from typing import get_args

from pydantic import ValidationError

from ..nodes.catalog import CATALOG, NOTIFIER_CONFIGS, CiGateConfig, CiTriggerConfig, ConditionConfig, ConditionDraft
from ..schemas.contracts import CiDecision, Graph, Issue, Node

_ANALYZERS = {"scan_secrets", "scan_sca", "scan_sast", "scan_password_hashing"}
_TRIGGERS = {"manual_trigger", "schedule_trigger", "ci_trigger"}


def _config_error(node: Node, *, draft: bool) -> Issue | None:
    config = node.config
    if node.type == "ci_gate":
        try:
            CiGateConfig.model_validate(config)
        except ValidationError:
            return Issue(code="INVALID_CONFIG", message="Выберите порог CRITICAL или HIGH.", node_id=node.id, field="config.threshold")
        return None
    if node.type == "ci_trigger":
        try:
            CiTriggerConfig.model_validate(config)
        except ValidationError:
            return Issue(code="INVALID_CONFIG", message="Проверьте имя ветки.", node_id=node.id, field="config.branch")
        return None
    if node.type == "condition":
        allowed = {"field", "operator", "value"}
        try:
            if draft and config.keys() < allowed:
                ConditionDraft.model_validate(config)
                return None
            condition = ConditionConfig.model_validate(config)
        except ValidationError:
            return Issue(code="INVALID_CONFIG", message="Укажите поле, операцию и значение условия.", node_id=node.id, field="config")
        if condition.field == "ci.decision":
            if condition.operator not in {"eq", "ne"} or condition.value not in get_args(CiDecision):
                return Issue(code="INVALID_CONFIG", message="Для вердикта CI выберите passed, blocked или error и операцию eq или ne.", node_id=node.id, field="config.value")
        elif condition.field == "coverage.status":
            if not isinstance(condition.value, str) or condition.operator not in {"eq", "ne", "matches"}:
                return Issue(code="INVALID_CONFIG", message="Для покрытия нужна строка и операция eq, ne или matches.", node_id=node.id, field="config")
            if condition.operator == "matches" and condition.value:
                try:
                    re.compile(condition.value)
                except (re.error, RecursionError):
                    return Issue(code="INVALID_REGEX", message="Неверный шаблон регулярного выражения.", node_id=node.id, field="config.value")
        elif type(condition.value) is not int or condition.operator == "matches":
            return Issue(code="INVALID_CONFIG", message="Для счётчика требуется целое число и операция сравнения.", node_id=node.id, field="config")
        return None
    if node.type in NOTIFIER_CONFIGS:
        if draft and not config.get("recipients"):
            # A draft may be saved before recipients are filled in; running it is still blocked.
            config = {**config, "recipients": ["@placeholder"] if node.type == "notify_telegram" else ["draft@example.org"]}
        try:
            NOTIFIER_CONFIGS[node.type].model_validate(config)
        except ValidationError as error:
            first = error.errors()[0]
            field = str(first["loc"][0]) if first.get("loc") else "config"
            message = {
                "recipients": "Укажите получателей: корректные chat_id / @канал или адреса почты, от 1 до 50.",
                "subject": "Тема письма должна быть не длиннее 200 символов.",
            }.get(field, "Проверьте настройки уведомления.")
            return Issue(code="INVALID_CONFIG", message=message, node_id=node.id, field=f"config.{field}")
        return None
    if config:
        return Issue(code="INVALID_CONFIG", message="У этого типа ноды нет настроек графа.", node_id=node.id, field="config")
    return None


def validate_draft(graph: Graph) -> list[Issue]:
    issues = []
    for node in graph.nodes:
        if node.type not in CATALOG:
            issues.append(Issue(code="UNKNOWN_NODE_TYPE", message="Тип ноды отсутствует в каталоге.", node_id=node.id, field="type"))
        elif issue := _config_error(node, draft=True):
            issues.append(issue)
    return issues


def validate_graph(graph: Graph, *, expected_trigger: str | None = None) -> list[Issue]:
    issues: list[Issue] = []
    nodes: dict[str, Node] = {}
    for node in graph.nodes:
        if node.id in nodes:
            issues.append(Issue(code="DUPLICATE_NODE", message="ID ноды повторяется.", node_id=node.id))
            continue
        if node.type not in CATALOG:
            issues.append(Issue(code="UNKNOWN_NODE_TYPE", message="Тип ноды отсутствует в каталоге.", node_id=node.id, field="type"))
        elif issue := _config_error(node, draft=False):
            issues.append(issue)
        nodes[node.id] = node
    counts = Counter(node.type for node in nodes.values())
    triggers = [node for node in nodes.values() if node.type in _TRIGGERS]
    if len(triggers) != 1:
        issues.append(Issue(code="TRIGGER_COUNT", message="Требуется ровно один триггер."))
    elif expected_trigger and triggers[0].type != expected_trigger:
        issues.append(Issue(code="WRONG_TRIGGER", message="Тип триггера не подходит для этого запуска.", node_id=triggers[0].id))
    for kind in ("repository", "merge_findings"):
        if counts[kind] != 1:
            issues.append(Issue(code="REQUIRED_NODE_COUNT", message=f"Требуется одна нода {kind}."))
    if not _ANALYZERS & counts.keys():
        issues.append(Issue(code="ANALYZER_REQUIRED", message="Подключите хотя бы один анализатор."))
    for kind in ("ai_report", "ci_gate"):
        if counts[kind] > 1:
            issues.append(Issue(code="NODE_LIMIT", message=f"Допускается одна нода {kind}."))

    edge_ids: set[str] = set()
    edges: set[tuple[str, str, str, str]] = set()
    incoming: Counter[tuple[str, str]] = Counter()
    outgoing: dict[str, list[str]] = defaultdict(list)
    in_degree = dict.fromkeys(nodes, 0)
    for edge in graph.edges:
        if edge.id in edge_ids:
            issues.append(Issue(code="DUPLICATE_EDGE", message="ID ребра повторяется.", edge_id=edge.id))
            continue
        edge_ids.add(edge.id)
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None:
            issues.append(Issue(code="UNKNOWN_EDGE_NODE", message="Один из концов ребра отсутствует.", edge_id=edge.id))
            continue
        if source.type not in CATALOG or target.type not in CATALOG:
            continue
        source_port = next((port for port in CATALOG[source.type].outputs if port.id == edge.source_port), None)
        target_port = next((port for port in CATALOG[target.type].inputs if port.id == edge.target_port), None)
        if source_port is None or target_port is None:
            issues.append(Issue(code="UNKNOWN_PORT", message="Порт ребра отсутствует в каталоге.", edge_id=edge.id))
            continue
        if not target_port.compatible(source_port.type):
            issues.append(Issue(code="INCOMPATIBLE_PORTS", message="Типы портов несовместимы.", edge_id=edge.id))
            continue
        connection = edge.source, edge.source_port, edge.target, edge.target_port
        if connection in edges:
            issues.append(Issue(code="DUPLICATE_CONNECTION", message="Это соединение уже есть.", edge_id=edge.id))
            continue
        edges.add(connection)
        incoming[edge.target, edge.target_port] += 1
        outgoing[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    for node in nodes.values():
        if node.type not in CATALOG:
            continue
        for port in CATALOG[node.type].inputs:
            count = incoming[node.id, port.id]
            if port.required and not count:
                issues.append(Issue(code="MISSING_INPUT", message=f"Подключите вход {port.id}.", node_id=node.id, field=port.id))
            elif not port.multiple and count > 1:
                issues.append(Issue(code="MULTIPLE_INPUTS", message=f"Вход {port.id} принимает одно соединение.", node_id=node.id, field=port.id))
    ready = deque(node_id for node_id, degree in in_degree.items() if degree == 0)
    visited = 0
    while ready:
        visited += 1
        for target_id in outgoing[ready.popleft()]:
            in_degree[target_id] -= 1
            if in_degree[target_id] == 0:
                ready.append(target_id)
    if visited != len(nodes):
        issues.append(Issue(code="GRAPH_CYCLE", message="Граф содержит цикл."))
    if len(triggers) == 1:
        reachable = {triggers[0].id}
        pending = [triggers[0].id]
        while pending:
            for target_id in outgoing[pending.pop()]:
                if target_id not in reachable:
                    reachable.add(target_id)
                    pending.append(target_id)
        for node_id in nodes.keys() - reachable:
            issues.append(Issue(code="UNREACHABLE_NODE", message="Нода недостижима из триггера.", node_id=node_id))
    if counts["ci_trigger"] == 1 and not issues:
        if counts["ci_gate"] != 1:
            return [Issue(code="CI_GATE_REQUIRED", message="Добавьте ноду «Порог CI».")]
        merge = next(node for node in nodes.values() if node.type == "merge_findings")
        gate = next(node for node in nodes.values() if node.type == "ci_gate")
        if gate.id not in outgoing[merge.id]:
            return [Issue(code="CI_GATE_INPUT", message="Подключите порог CI напрямую к объединению находок.", node_id=gate.id)]
        for node in nodes.values():
            if node.type in _ANALYZERS and merge.id not in outgoing[node.id]:
                issues.append(Issue(code="CI_SCAN_INPUT", message="Подключите анализатор к объединению находок.", node_id=node.id))
    return issues
