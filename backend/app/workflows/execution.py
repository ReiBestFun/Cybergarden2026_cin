from collections import defaultdict
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from graphlib import TopologicalSorter
from operator import eq, ge, gt, le, lt, ne
from typing import Literal

import regex
from pydantic import ValidationError

from ..analyzers.models import Category, ScanError, Warning, finding_key
from ..config import Settings
from ..integrations.repository import RepositorySnapshot, prepare_repository
from ..nodes.catalog import ConditionConfig
from ..reports import NodeResult, RunReport, merge_findings
from ..schemas.contracts import Edge, Graph, Node
from ..worker import run_scan
from .persistence import RunProgress, error_payload
from .validation import validate_graph

SCANS: dict[str, Category] = {"scan_secrets": "secrets", "scan_sca": "sca", "scan_sast": "sast"}
_COMPARISONS = {"eq": eq, "ne": ne, "gt": gt, "gte": ge, "lt": lt, "lte": le}
_INACTIVE = Warning("INACTIVE_BRANCH", "Вход находится в невыбранной ветке.")
_UPSTREAM_FAILED = Warning("UPSTREAM_FAILED", "Вход недоступен из-за ошибки предыдущей ноды.")
_NO_RESULTS = Warning("NO_SCAN_RESULTS", "Ни одна подключённая проверка не дала результата.")
_INTEGRATION_UNAVAILABLE = Warning("INTEGRATION_UNAVAILABLE", "Эта интеграция пока недоступна в воркере.")


@dataclass(frozen=True)
class Step:
    status: Literal["succeeded", "failed", "skipped"] = "succeeded"
    ports: tuple[str, ...] = ()
    preview: dict | None = None
    warning: Warning | None = None
    scan: NodeResult | None = None
    bundle: RunReport | None = None


def _coverage_status(report: RunReport) -> str:
    return {"succeeded": "complete", "partial": "partial", "failed": "unavailable"}[report.status]


def _condition_branch(config: ConditionConfig, bundle: RunReport) -> str:
    if config.field == "coverage.status":
        actual = _coverage_status(bundle)
    else:
        actual = getattr(bundle.summary, config.field.removeprefix("summary."))
    try:
        matches = (
            regex.search(config.value, actual, timeout=0.05) is not None
            if config.operator == "matches" else _COMPARISONS[config.operator](actual, config.value)
        )
    except TimeoutError:
        raise ScanError(Warning("CONDITION_TIMEOUT", "Проверка регулярного выражения превысила лимит времени.")) from None
    except regex.error:
        raise ScanError(Warning("INVALID_REGEX", "Не удалось обработать регулярное выражение.")) from None
    return "true" if matches else "false"


def _scan_preview(outcome: NodeResult) -> dict:
    payload = {
        "category": outcome.category, "status": outcome.status,
        "coverage": None, "warnings": [], "finding_count": 0,
    }
    if outcome.result is not None:
        payload.update(
            coverage=asdict(outcome.result.coverage),
            warnings=[asdict(warning) for warning in outcome.result.warnings],
            finding_count=len(outcome.result.findings),
        )
    if outcome.warning is not None:
        payload["warnings"].append(asdict(outcome.warning))
    return payload


class GraphExecution:
    def __init__(self, progress: RunProgress, settings: Settings, allowed: frozenset[str]):
        self.progress = progress
        self.settings = settings
        self.allowed = allowed
        self.source = progress.claim.source
        self.prepared: RepositorySnapshot | None = None
        self.steps: dict[str, Step] = {}
        self.scans: dict[str, NodeResult] = {}
        self.finding_keys: set[tuple] = set()

    @property
    def sha(self) -> str:
        return self.prepared.snapshot.sha if self.prepared else ""

    def _active(self, edge: Edge) -> bool:
        return edge.source_port in self.steps[edge.source].ports

    def _execute_node(self, node: Node, edges: list[Edge], resources: ExitStack) -> Step:
        if node.type in {"manual_trigger", "schedule_trigger"}:
            return Step(ports=("params",), preview={"trigger": self.progress.claim.trigger, "ref": self.source.get("ref")})
        if node.type == "repository":
            url, ref = self.source.get("repository_url"), self.source.get("ref")
            if not isinstance(url, str) or not isinstance(ref, str):
                raise ScanError(Warning("INVALID_SOURCE", "В запуске отсутствуют параметры репозитория."))
            self.prepared = resources.enter_context(prepare_repository(
                url, ref, allowed=self.allowed, timeout=self.settings.repository_timeout_seconds,
                catalog_path=self.settings.sca_catalog_path,
            ))
            return Step(ports=("snapshot",), preview={"commit_sha": self.sha, "files": len(self.prepared.snapshot.files)})
        if node.type in SCANS:
            assert self.prepared is not None
            outcome = run_scan(
                SCANS[node.type], self.prepared.snapshot, self.prepared.catalog_path, self.settings.scan_timeout_seconds,
            )
            ports = ("result",) if outcome.status == "succeeded" else ()
            return Step(outcome.status, ports, _scan_preview(outcome), outcome.warning, scan=outcome)
        if node.type == "merge_findings":
            bundle = merge_findings(self.sha, tuple(self.scans[edge.source] for edge in edges))
            preview = {"summary": asdict(bundle.summary), "coverage_status": _coverage_status(bundle)}
            if bundle.status == "failed":
                return Step("failed", preview=preview, warning=_NO_RESULTS)
            return Step(ports=("bundle",), preview=preview, bundle=bundle)
        if node.type == "condition":
            bundle = self.steps[edges[0].source].bundle
            assert bundle is not None
            branch = _condition_branch(ConditionConfig.model_validate(node.config), bundle)
            return Step(ports=(branch,), preview={"branch": branch}, bundle=bundle)
        return Step("failed", warning=_INTEGRATION_UNAVAILABLE)

    def _run_node(self, node: Node, inputs: list[Edge], resources: ExitStack) -> Step:
        active = [edge for edge in inputs if self._active(edge)]
        if node.type != "merge_findings" and len(active) < len(inputs):
            upstream_failed = any(
                self.steps[edge.source].status == "failed"
                or self.steps[edge.source].warning == _UPSTREAM_FAILED
                for edge in inputs
            )
            return Step("skipped", warning=_UPSTREAM_FAILED if upstream_failed else _INACTIVE)
        self.progress.start_node(node.id, {"inputs": [
            {"node_id": edge.source, "port": edge.source_port} for edge in active
        ]})
        try:
            return self._execute_node(node, inputs, resources)
        except ScanError as error:
            return Step("failed", warning=error.warning)

    def _record(self, node: Node, step: Step) -> None:
        self.steps[node.id] = step
        findings = []
        if node.type in SCANS:
            outcome = step.scan or NodeResult(SCANS[node.type], step.status, warning=step.warning)
            self.scans[node.id] = outcome
            if outcome.result is not None:
                for finding in outcome.result.findings:
                    key = finding_key(finding)
                    if key not in self.finding_keys:
                        findings.append(finding)
                        self.finding_keys.add(key)
        self.progress.finish_node(
            node.id, step.status, ports=list(step.ports), preview=step.preview, warning=step.warning,
            findings=tuple(findings), sha=self.sha if node.type == "repository" and self.sha else None,
        )

    def execute(self) -> None:
        try:
            graph = Graph.model_validate(self.progress.claim.graph)
        except ValidationError:
            self.progress.fail(Warning("INVALID_GRAPH", "Снимок графа имеет неверный формат."))
            return
        if issues := validate_graph(graph, expected_trigger=f"{self.progress.claim.trigger}_trigger"):
            self.progress.fail(Warning("INVALID_GRAPH", issues[0].message))
            return

        incoming: dict[str, list[Edge]] = defaultdict(list)
        for edge in graph.edges:
            incoming[edge.target].append(edge)
        nodes = {node.id: node for node in graph.nodes}
        order = TopologicalSorter({
            node_id: [edge.source for edge in incoming[node_id]] for node_id in nodes
        }).static_order()

        with ExitStack() as resources:
            for node_id in order:
                node = nodes[node_id]
                self._record(node, self._run_node(node, incoming[node_id], resources))

        report = merge_findings(self.sha, tuple(self.scans.values()))
        status = report.status
        failure = None
        merged = next(self.steps[node.id] for node in graph.nodes if node.type == "merge_findings")
        if merged.status == "failed":
            status = "failed"
            failure = next((step.warning for step in self.steps.values() if step.status == "failed"), _NO_RESULTS)
        elif any(step.status == "failed" for step in self.steps.values()):
            status = "partial"
        payload = {"coverage": {
            "status": _coverage_status(report),
            "checks": {node_id: _scan_preview(outcome) for node_id, outcome in self.scans.items()},
        }}
        for node in graph.nodes:
            key = {"ai_report": "llm", "notify_telegram": "delivery"}.get(node.type)
            if key:
                step = self.steps[node.id]
                payload[key] = {"status": step.status, "error": error_payload(step.warning)}
        self.progress.finish_run(status, asdict(report.summary), payload, failure)
