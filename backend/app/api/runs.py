from collections import defaultdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import Select, func, select

from ..analyzers.models import Category, Coverage, Finding, ScanResult, Severity, Warning
from ..db.models import FindingRow, NodeRun, ScanRun
from ..db.session import Database
from ..reports import NodeResult, RunReport, readable_report, submission_json
from ..schemas.contracts import (
    FindingRead, Graph, NodeRunRead, Page, RunCreate, RunDetail, RunQueued,
    RunRead, RunReportRead, RunStatus, RunSummary,
)
from ..workflows.queue import enqueue_manual_run
from ..workflows.validation import validate_graph
from .common import ApiError, Pagination, graph_error, page
from .projects import get_project
from .workflows import get_workflow

router = APIRouter(tags=["runs"])


class RunFilters(Pagination):
    status: RunStatus | None = None
    workflow_id: UUID | None = None


class FindingFilters(Pagination):
    category: Category | None = None
    severity: Severity | None = None


def _get_run(run_id: UUID, db: Database) -> ScanRun:
    run = db.get(ScanRun, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "Запуск не найден.")
    return run


def _findings(run_id: UUID) -> Select[tuple[FindingRow]]:
    return select(FindingRow).where(FindingRow.run_id == run_id).order_by(
        FindingRow.path, FindingRow.line.nulls_last(), FindingRow.id
    )


@router.post("/workflows/{workflow_id}/runs", status_code=202, response_model=RunQueued)
def create_run(workflow_id: UUID, db: Database, data: RunCreate | None = None) -> ScanRun:
    workflow = get_workflow(workflow_id, db, lock=True)
    graph = Graph.model_validate(workflow.graph_json)
    if issues := validate_graph(graph, expected_trigger="manual_trigger"):
        raise graph_error(issues[0])
    project = get_project(workflow.project_id, db)
    ref = data.ref if data and data.ref is not None else project.default_ref
    run = enqueue_manual_run(db, project, workflow, graph, ref)
    db.commit()
    return run


@router.get("/projects/{project_id}/runs", response_model=Page[RunRead])
def list_runs(project_id: UUID, db: Database, filters: Annotated[RunFilters, Query()]) -> dict:
    get_project(project_id, db)
    statement = select(ScanRun).where(ScanRun.project_id == project_id)
    if filters.status is not None:
        statement = statement.where(ScanRun.status == filters.status)
    if filters.workflow_id is not None:
        statement = statement.where(ScanRun.workflow_id == filters.workflow_id)
    statement = statement.order_by(ScanRun.created_at.desc(), ScanRun.id.desc())
    return page(db, statement, filters.limit, filters.offset)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: UUID, db: Database) -> RunDetail:
    run = _get_run(run_id, db)
    graph = Graph.model_validate(run.graph_snapshot)
    nodes = {
        node.node_id: node
        for node in db.scalars(select(NodeRun).where(NodeRun.run_id == run_id))
    }
    return RunDetail(
        **RunRead.model_validate(run).model_dump(),
        graph_snapshot=graph,
        nodes=[NodeRunRead.model_validate(nodes[node.id]) for node in graph.nodes if node.id in nodes],
    )


@router.get("/runs/{run_id}/findings", response_model=Page[FindingRead])
def list_findings(run_id: UUID, db: Database, filters: Annotated[FindingFilters, Query()]) -> dict:
    _get_run(run_id, db)
    statement = _findings(run_id)
    if filters.category is not None:
        statement = statement.where(FindingRow.category == filters.category)
    if filters.severity is not None:
        statement = statement.where(FindingRow.severity == filters.severity)
    return page(db, statement, filters.limit, filters.offset)


@router.get("/runs/{run_id}/report", response_model=RunReportRead)
def get_report(run_id: UUID, db: Database) -> RunReportRead:
    run = _get_run(run_id, db)
    payload = run.report or {}
    if run.summary is None:
        statement = (
            select(FindingRow.severity, func.count())
            .where(FindingRow.run_id == run_id)
            .group_by(FindingRow.severity)
        )
        counts = {severity: count for severity, count in db.execute(statement)}
        summary = RunSummary(total=sum(counts.values()), high_count=counts.get("high", 0), critical_count=counts.get("critical", 0))
    else:
        summary = RunSummary.model_validate(run.summary)
    return RunReportRead(
        status=run.status, summary=summary, coverage=payload.get("coverage", {}),
        llm=payload.get("llm"), delivery=payload.get("delivery"), error=run.error,
    )


@router.get(
    "/runs/{run_id}/export", response_class=Response,
    responses={200: {"content": {"application/json": {}, "text/markdown": {}}, "description": "Файл отчёта"}},
)
def export_run(run_id: UUID, db: Database, format: Literal["json", "markdown"] = "json") -> Response:
    run = _get_run(run_id, db)
    if run.status in {"queued", "running"}:
        raise ApiError(409, "REPORT_NOT_READY", "Запуск ещё выполняется.")
    if run.status == "failed":
        raise ApiError(409, "REPORT_UNAVAILABLE", "Для этого запуска отчёт недоступен.")
    rows = tuple(db.scalars(_findings(run_id)))
    findings = tuple(
        Finding(
            rule_id=row.rule_id, category=row.category, severity=row.severity, title=row.title,
            description=row.description, remediation=row.remediation, path=row.path, line=row.line,
            end_line=row.end_line, evidence=row.evidence, metadata=row.details,
        )
        for row in rows
    )
    report = RunReport(run.commit_sha or "", run.status, (), findings)
    if format == "json":
        body, media_type, extension = submission_json(report), "application/json", "json"
    else:
        by_node_run: dict[UUID, list[Finding]] = defaultdict(list)
        for row, finding in zip(rows, findings):
            by_node_run[row.node_run_id].append(finding)
        checks = (run.report or {}).get("coverage", {}).get("checks", {})
        nodes = []
        for node in db.scalars(select(NodeRun).where(NodeRun.run_id == run_id).order_by(NodeRun.started_at, NodeRun.node_id)):
            check = checks.get(node.node_id)
            if check is None:
                continue
            category = check["category"]
            warnings = tuple(Warning(**warning) for warning in check["warnings"])
            coverage = check["coverage"]
            if coverage is None:
                nodes.append(NodeResult(category, check["status"], warning=next(iter(warnings), None)))
            else:
                scan = ScanResult(category, tuple(by_node_run[node.id]), warnings, Coverage(**coverage))
                nodes.append(NodeResult(category, check["status"], result=scan))
        report = RunReport(report.sha, report.status, tuple(nodes), report.findings)
        body, media_type, extension = readable_report(report), "text/markdown", "md"
    return Response(
        body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="report-{run.id}.{extension}"'},
    )
