from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from graphlib import TopologicalSorter
from operator import eq, ge, gt, le, lt, ne
from typing import Literal

import regex
from pydantic import ValidationError

from ..ai_reports import ReportError, explain_report
from ..analyzers.models import Finding, ScanError, Warning, finding_key
from ..analyzers.registry import SCANNER_NODES, SCANNERS
from ..config import Settings
from ..integrations.repository import Allowlist, RepositorySnapshot, ci_repository_allowlist, prepare_repository
from ..nodes.catalog import NOTIFIER_CONFIGS, CiGateConfig, ConditionConfig, ConditionExpression, ConditionGroup, TelegramConfig
from ..notifications import build_message
from ..notifications.content import build_slack_message
from ..notifications.queue import NotificationDraft, allowed_recipients
from ..notifications.slack import SLACK_CHANNEL_RECIPIENT
from ..reports import NodeResult, RunReport, merge_findings
from ..schemas.contracts import CiVerdict, Edge, Graph, Node
from ..worker import run_scan
from .persistence import RunProgress, error_payload
from .validation import validate_graph

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
    ci_verdict: CiVerdict | None = None
    input_preview: dict | None = None
    notification: NotificationDraft | None = None


def _coverage_status(report: RunReport) -> str:
    return {"succeeded": "complete", "partial": "partial", "failed": "unavailable"}[report.status]


def evaluate_ci_gate(config: CiGateConfig, bundle: RunReport) -> CiVerdict:
    summary = bundle.summary
    count = summary.critical_count + (summary.high_count if config.threshold == "HIGH" else 0)
    if count:
        decision, reason = "blocked", "THRESHOLD_REACHED"
    elif bundle.status != "succeeded":
        decision, reason = "error", "SCAN_INCOMPLETE"
    else:
        decision, reason = "passed", "CHECKS_PASSED"
    return CiVerdict(decision=decision, reason=reason, threshold=config.threshold, findings_at_threshold=count)


def _matches_condition(config: ConditionConfig, bundle: RunReport, verdict: CiVerdict | None) -> bool:
    if config.field == "ci.decision":
        if verdict is None:
            raise ScanError(Warning("CI_VERDICT_MISSING", "Для проверки вердикта CI проведите данные через ноду «Порог CI»."))
        actual = verdict.decision
    elif config.field == "coverage.status":
        actual = _coverage_status(bundle)
    else:
        actual = getattr(bundle.summary, config.field.removeprefix("summary."))
    try:
        return (
            regex.search(config.value, actual, timeout=0.05) is not None
            if config.operator == "matches" else _COMPARISONS[config.operator](actual, config.value)
        )
    except TimeoutError:
        raise ScanError(Warning("CONDITION_TIMEOUT", "Проверка регулярного выражения превысила лимит времени.")) from None
    except regex.error:
        raise ScanError(Warning("INVALID_REGEX", "Не удалось обработать регулярное выражение.")) from None


def _condition_branch(config: ConditionConfig | ConditionGroup, bundle: RunReport, verdict: CiVerdict | None) -> str:
    if isinstance(config, ConditionGroup):
        combine = all if config.mode == "AND" else any
        matches = combine(_matches_condition(rule, bundle, verdict) for rule in config.conditions)
    else:
        matches = _matches_condition(config, bundle, verdict)
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
            findings=_finding_preview(outcome.result.findings),
        )
    if outcome.warning is not None:
        payload["warnings"].append(asdict(outcome.warning))
    return payload


def _finding_preview(findings: tuple[Finding, ...]) -> list[dict]:
    return [
        {"category": item.category, "severity": item.severity, "title": item.title, "path": item.path, "line": item.line}
        for item in findings[:15]
    ]


def _bundle_preview(bundle: RunReport, verdict: CiVerdict | None = None) -> dict:
    data = {"summary": asdict(bundle.summary), "coverage_status": _coverage_status(bundle), "findings": _finding_preview(bundle.findings)}
    if verdict is not None:
        data["ci"] = verdict.model_dump()
    return data


class GraphExecution:
    def __init__(self, progress: RunProgress, settings: Settings, allowed: Allowlist):
        self.progress = progress
        self.settings = settings
        self.allowed = ci_repository_allowlist(settings.allowed_repositories) if progress.claim.trigger == "ci" else allowed
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
        if node.type in {"manual_trigger", "schedule_trigger", "ci_trigger"}:
            return Step(ports=("params",), preview={"trigger": self.progress.claim.trigger, "ref": self.source.get("ref")})
        if node.type == "repository":
            url, ref = self.source.get("repository_url"), self.source.get("ref")
            if not isinstance(url, str) or not isinstance(ref, str):
                raise ScanError(Warning("INVALID_SOURCE", "В запуске отсутствуют параметры репозитория."))
            prepared = resources.enter_context(prepare_repository(
                url, ref, allowed=self.allowed, timeout=self.settings.repository_timeout_seconds,
                catalog_path=self.settings.sca_catalog_path,
                pin_training_catalog=self.progress.claim.trigger == "ci",
            ))
            if self.progress.claim.trigger == "ci" and prepared.snapshot.sha != ref:
                raise ScanError(Warning("CI_SHA_MISMATCH", "Полученный коммит не совпадает с SHA из CI."))
            self.prepared = prepared
            return Step(ports=("snapshot",), preview={"commit_sha": self.sha, "files": len(self.prepared.snapshot.files)})
        if node.type in SCANNER_NODES:
            assert self.prepared is not None
            outcome = run_scan(
                SCANNER_NODES[node.type], self.prepared.snapshot, self.prepared.catalog_path, self.settings.scan_timeout_seconds,
                run_id=self.progress.claim.id,
            )
            ports = ("result",) if outcome.status == "succeeded" else ()
            return Step(outcome.status, ports, _scan_preview(outcome), outcome.warning, scan=outcome)
        if node.type == "merge_findings":
            bundle = merge_findings(self.sha, tuple(self.scans[edge.source] for edge in edges))
            preview = _bundle_preview(bundle)
            if bundle.status == "failed":
                return Step("failed", preview=preview, warning=_NO_RESULTS)
            return Step(ports=("bundle",), preview=preview, bundle=bundle)
        if node.type == "condition":
            upstream = self.steps[edges[0].source]
            bundle = upstream.bundle
            assert bundle is not None
            branch = _condition_branch(ConditionExpression.model_validate(node.config).root, bundle, upstream.ci_verdict)
            preview = {"branch": branch, **_bundle_preview(bundle, upstream.ci_verdict)}
            return Step(ports=(branch,), preview=preview, bundle=bundle, ci_verdict=upstream.ci_verdict)
        if node.type == "ci_gate":
            bundle = self.steps[edges[0].source].bundle
            assert bundle is not None
            gate = evaluate_ci_gate(CiGateConfig.model_validate(node.config), bundle)
            return Step(ports=("bundle",), preview={**gate.model_dump(), **_bundle_preview(bundle)}, bundle=bundle, ci_verdict=gate)
        if node.type in NOTIFIER_CONFIGS:
            return self._notify(node, edges)
        if node.type == "ai_report":
            bundle = self.steps[edges[0].source].bundle
            assert bundle is not None
            try:
                settings = self.progress.llm_settings(self.settings)
                text = explain_report(bundle, settings)
            except ReportError as error:
                # Keep the scanner report available to downstream notifications.
                return Step("failed", ports=("report",), bundle=bundle,
                            warning=Warning("AI_REPORT_UNAVAILABLE", str(error)))
            return Step(ports=("report",), bundle=bundle, preview={"text": text, "model": settings.llm_model})
        return Step("failed", warning=_INTEGRATION_UNAVAILABLE)

    def _notify(self, node: Node, edges: list[Edge]) -> Step:
        bundle = next((self.steps[edge.source].bundle for edge in edges if self._active(edge)), None)
        if bundle is None:
            raise ScanError(Warning("NO_REPORT", "На вход уведомления не пришёл отчёт."))
        if node.type == "notify_slack":
            settings = self.progress.delivery_settings("slack")
            if settings is None or not settings.secret:
                raise ScanError(Warning("SLACK_NOT_CONFIGURED", "Подключите Slack в настройках команды."))
            message = build_slack_message(bundle, run_id=str(self.progress.claim.id), app_base_url=self.settings.app_base_url)
            draft = NotificationDraft(
                "slack", (SLACK_CHANNEL_RECIPIENT,), None, message, settings.workspace_id, settings.version,
            )
        else:
            draft = self._mail_or_telegram(node, edges, bundle)
        return Step(
            ports=("delivery",),
            preview={"channel": draft.channel, "status": "queued", "attachments": [item.filename for item in draft.message.attachments]},
            notification=draft,
        )

    def _mail_or_telegram(self, node: Node, edges: list[Edge], bundle: RunReport) -> NotificationDraft:
        config = NOTIFIER_CONFIGS[node.type].model_validate(node.config)
        summary_only = isinstance(config, TelegramConfig) and config.summary_only
        message = build_message(
            bundle, run_id=str(self.progress.claim.id), repository=str(self.source.get("repository_url", "")),
            app_base_url=self.settings.app_base_url, attach_json=config.attach_json,
            max_attachment_bytes=self.settings.notify_max_attachment_bytes, summary_only=summary_only,
        )
        if getattr(config, "subject", None):
            message = replace(message, subject=config.subject)
        upstream = self.steps[edges[0].source]
        if edges[0].source_port == "report" and not summary_only:
            explanation = (upstream.preview or {}).get("text") or "AI-рекомендации недоступны."
            message = replace(message, text=message.text + "\n\n" + explanation)
        channel = node.type.removeprefix("notify_")
        allowed = allowed_recipients(self.settings, channel)
        recipients = tuple(config.recipients)
        workspace_id = None
        if config.use_workspace_settings:
            settings = self.progress.delivery_settings(channel)
            if settings is None or not settings.recipients:
                raise ScanError(Warning("WORKSPACE_DELIVERY_UNCONFIGURED", "Настройте получателей команды для этого канала."))
            workspace_id, recipients = settings.workspace_id, tuple(settings.recipients)
        return NotificationDraft(channel, recipients, allowed, message, workspace_id)

    def _run_node(self, node: Node, inputs: list[Edge], resources: ExitStack) -> Step:
        active = [edge for edge in inputs if self._active(edge)]
        preview = {"inputs": []}
        for edge in inputs:
            source = self.steps[edge.source]
            passed = edge.source_port in source.ports
            preview["inputs"].append({
                "edge_id": edge.id, "node_id": edge.source, "port": edge.source_port, "target_port": edge.target_port,
                "status": "available" if passed else "failed" if source.status == "failed" else "skipped",
                "data": source.preview if passed else None,
            })
        if node.type != "merge_findings" and len(active) < len(inputs):
            upstream_failed = any(
                self.steps[edge.source].status == "failed"
                or self.steps[edge.source].warning == _UPSTREAM_FAILED
                for edge in inputs
            )
            return Step("skipped", warning=_UPSTREAM_FAILED if upstream_failed else _INACTIVE, input_preview=preview)
        self.progress.start_node(node.id, preview)
        try:
            return self._execute_node(node, inputs, resources)
        except ScanError as error:
            return Step("failed", warning=error.warning)

    def _record(self, node: Node, step: Step) -> None:
        self.steps[node.id] = step
        findings = []
        if node.type in SCANNER_NODES:
            category = SCANNERS[SCANNER_NODES[node.type]].category
            outcome = step.scan or NodeResult(category, step.status, warning=step.warning)
            self.scans[node.id] = outcome
            if outcome.result is not None:
                for finding in outcome.result.findings:
                    key = finding_key(finding)
                    if key not in self.finding_keys:
                        findings.append(finding)
                        self.finding_keys.add(key)
        self.progress.finish_node(
            node.id, step.status, ports=list(step.ports), preview=step.preview, input_preview=step.input_preview, warning=step.warning,
            findings=tuple(findings), sha=self.sha if node.type == "repository" and self.sha else None,
            notification=step.notification,
        )

    def execute(self) -> None:
        try:
            graph = Graph.model_validate(self.progress.claim.graph)
        except ValidationError:
            self.progress.fail(Warning("INVALID_GRAPH", "Снимок графа имеет неверный формат."))
            return
        expected = {"schedule": "schedule_trigger", "ci": "ci_trigger"}.get(self.progress.claim.trigger)
        if issues := validate_graph(graph, expected_trigger=expected):
            self.progress.fail(Warning("INVALID_GRAPH", issues[0].message))
            return

        incoming: dict[str, list[Edge]] = defaultdict(list)
        for edge in graph.edges:
            incoming[edge.target].append(edge)
        nodes = {node.id: node for node in graph.nodes}
        order = TopologicalSorter({
            node_id: [edge.source for edge in incoming[node_id]] for node_id in nodes
        })
        order.prepare()

        with ExitStack() as resources, ThreadPoolExecutor(max_workers=self.settings.scan_parallelism) as pool:
            while order.is_active():
                ready = order.get_ready()
                scans = {
                    node_id: pool.submit(self._run_node, nodes[node_id], incoming[node_id], resources)
                    for node_id in ready if nodes[node_id].type in SCANNER_NODES
                }
                for node_id in ready:
                    node = nodes[node_id]
                    scan = scans.get(node_id)
                    step = scan.result() if scan is not None else self._run_node(node, incoming[node_id], resources)
                    self._record(node, step)
                    order.done(node_id)

        report = merge_findings(self.sha, tuple(self.scans.values()))
        status = report.status
        failure = None
        merged = next(self.steps[node.id] for node in graph.nodes if node.type == "merge_findings")
        if merged.status == "failed":
            status = "failed"
            failure = next((step.warning for step in self.steps.values() if step.status == "failed"), _NO_RESULTS)
        elif any(self.steps[node.id].status == "failed" for node in graph.nodes if node.type not in NOTIFIER_CONFIGS):
            status = "partial"
        payload = {"coverage": {
            "status": _coverage_status(report),
            "checks": {node_id: _scan_preview(outcome) for node_id, outcome in self.scans.items()},
        }}
        for node in graph.nodes:
            step = self.steps[node.id]
            if node.type == "ai_report":
                payload["llm"] = {"status": step.status, "error": error_payload(step.warning), **(step.preview or {})}
            elif node.type == "ci_gate" and step.ci_verdict is not None:
                payload["ci_gate"] = step.ci_verdict.model_dump()
            elif node.type in NOTIFIER_CONFIGS:
                payload.setdefault("delivery", {})[node.id] = {
                    "status": "queued" if step.notification else step.status, "error": error_payload(step.warning),
                    "channel": node.type.removeprefix("notify_"),
                }
        self.progress.finish_run(status, asdict(report.summary), payload, failure)
