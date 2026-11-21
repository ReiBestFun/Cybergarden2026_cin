from collections import defaultdict
from collections.abc import Iterator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from psycopg import connect, sql
from sqlalchemy import Select, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from ..analyzers.models import Category, Coverage, Finding, ScanResult, Severity, Warning
from ..db.models import User, FindingRow, NodeRun, Project, ScanRun
from ..db.session import Database
from ..db.workspaces import project_access
from ..nodes.catalog import NOTIFIER_CONFIGS
from ..notifications.queue import delivery_summary
from ..reports import NodeResult, RunReport, readable_report, submission_json
from ..schemas.contracts import (
    FindingRead, Graph, NodeRunRead, Page, RunCreate, RunDetail, RunQueued,
    RunRead, RunReportRead, RunStatus, RunSummary,
)
from ..workflows.queue import enqueue_manual_run
from ..workflows.validation import validate_graph
from .auth import CurrentUser
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


def _get_run(run_id: UUID, db: Database, user_id: UUID, request: Request | None = None) -> ScanRun:
    run = db.scalar(select(ScanRun).join(Project, Project.id == ScanRun.project_id).where(
        ScanRun.id == run_id, project_access(user_id),
    ))
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "Запуск не найден.")
    if request is not None:
        request.state.run_id = run.id
    return run


def _findings(run_id: UUID) -> Select[tuple[FindingRow]]:
    return select(FindingRow).where(FindingRow.run_id == run_id).order_by(
        FindingRow.category, FindingRow.line.nulls_last(), FindingRow.id
    )


@router.post("/workflows/{workflow_id}/runs", status_code=202, response_model=RunQueued)
def create_run(workflow_id: UUID, db: Database, user: CurrentUser, request: Request, data: RunCreate | None = None) -> ScanRun:
    workflow = get_workflow(workflow_id, db, user.id, lock=True, owner_required=True)
    graph = Graph.model_validate(workflow.graph_json)
    # "Run now" works for any trigger: a scheduled workflow can also be started by hand.
    if issues := validate_graph(graph):
        raise graph_error(issues[0])
    project = get_project(workflow.project_id, db, user.id)
    ref = data.ref if data and data.ref is not None else project.default_ref
    run = enqueue_manual_run(db, project, workflow, graph, ref)
    db.commit()
    request.state.run_id = run.id
    return run


@router.get("/projects/{project_id}/runs", response_model=Page[RunRead])
def list_runs(project_id: UUID, db: Database, filters: Annotated[RunFilters, Query()], user: CurrentUser) -> dict:
    get_project(project_id, db, user.id)
    statement = select(ScanRun).where(ScanRun.project_id == project_id)
    if filters.status is not None:
        statement = statement.where(ScanRun.status == filters.status)
    if filters.workflow_id is not None:
        statement = statement.where(ScanRun.workflow_id == filters.workflow_id)
    statement = statement.order_by(ScanRun.created_at.desc(), ScanRun.id.desc())
    return page(db, statement, filters.limit, filters.offset)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: UUID, db: Database, user: CurrentUser, request: Request) -> RunDetail:
    return _run_detail(_get_run(run_id, db, user.id, request), db)


def _run_detail(run: ScanRun, db: Database) -> RunDetail:
    run_id = run.id
    graph = Graph.model_validate(run.graph_snapshot)
    nodes = {
        node.node_id: node
        for node in db.scalars(select(NodeRun).where(NodeRun.run_id == run_id))
    }
    delivered = delivery_summary(db, run_id) if any(node.type in NOTIFIER_CONFIGS for node in graph.nodes) else {}
    details = []
    for node in graph.nodes:
        if node.id not in nodes:
            continue
        detail = NodeRunRead.model_validate(nodes[node.id])
        if node.id in delivered:
            detail.output_preview = (detail.output_preview or {}) | delivered[node.id]
        details.append(detail)
    return RunDetail(
        **RunRead.model_validate(run).model_dump(),
        graph_snapshot=graph,
        nodes=details,
        delivery=(((run.report or {}).get("delivery") or {}) | delivered) or None,
    )


def _stream_finished(run: RunDetail, wait_for_delivery: bool) -> bool:
    if run.status in {"queued", "running"}:
        return False
    return not wait_for_delivery or not any(
        delivery.get("recipients") and delivery.get("status") in {"queued", "sending"}
        for delivery in (run.delivery or {}).values() if isinstance(delivery, dict)
    )


def _run_events(run: RunDetail, sessions: sessionmaker[Session], dsn: str, user: User, *, wait_for_delivery: bool = False) -> Iterator[str]:
    previous = run.model_dump_json()
    yield f"retry: 1500\nevent: run\ndata: {previous}\n\n"
    if _stream_finished(run, wait_for_delivery):
        return
    with connect(dsn, autocommit=True, connect_timeout=5) as listener:
        listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(f"cyberflow_run_{run.id.hex}")))
        while True:
            # Take a new snapshot after LISTEN to include changes since the first read.
            with sessions() as db:
                run = _run_detail(_get_run(run.id, db, user.id), db)
            current = run.model_dump_json()
            if current != previous:
                yield f"event: run\ndata: {current}\n\n"
                previous = current
            if _stream_finished(run, wait_for_delivery):
                return
            if not list(listener.notifies(timeout=10, stop_after=1)):
                yield ": heartbeat\n\n"


@router.get("/runs/{run_id}/events")
def stream_run(run_id: UUID, request: Request, user: CurrentUser, wait_for_delivery: bool = False) -> StreamingResponse:
    sessions = request.app.state.session_factory
    with sessions() as db:
        initial = _run_detail(_get_run(run_id, db, user.id, request), db)
    dsn = make_url(request.app.state.settings.database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    return StreamingResponse(
        _run_events(initial, sessions, dsn, user, wait_for_delivery=wait_for_delivery), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/findings", response_model=Page[FindingRead])
def list_findings(run_id: UUID, db: Database, filters: Annotated[FindingFilters, Query()], user: CurrentUser, request: Request) -> dict:
    _get_run(run_id, db, user.id, request)
    statement = _findings(run_id)
    if filters.category is not None:
        statement = statement.where(FindingRow.category == filters.category)
    if filters.severity is not None:
        statement = statement.where(FindingRow.severity == filters.severity)
    return page(db, statement, filters.limit, filters.offset)


@router.get("/runs/{run_id}/report", response_model=RunReportRead)
def get_report(run_id: UUID, db: Database, user: CurrentUser, request: Request) -> RunReportRead:
    run = _get_run(run_id, db, user.id, request)
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
    delivery = (payload.get("delivery") or {}) | delivery_summary(db, run_id)
    return RunReportRead(
        status=run.status, summary=summary, coverage=payload.get("coverage", {}),
        llm=payload.get("llm"), delivery=delivery or None, error=run.error,
    )


@router.get(
    "/runs/{run_id}/export", response_class=Response,
    responses={200: {"content": {"application/json": {}, "text/markdown": {}}, "description": "Файл отчёта"}},
)
def export_run(
    run_id: UUID, db: Database, user: CurrentUser, request: Request, format: Literal["json", "markdown"] = "json",
) -> Response:
    run = _get_run(run_id, db, user.id, request)
    if run.status in {"queued", "running"}:
        raise ApiError(409, "REPORT_NOT_READY", "Запуск ещё выполняется.")
    if run.status == "failed":
        raise ApiError(409, "REPORT_UNAVAILABLE", "Для этого запуска отчёт недоступен.")
    rows = sorted(db.scalars(_findings(run_id)), key=lambda row: (row.path, row.line is None, row.line or 0, row.id))
    findings = tuple(
        Finding(
            rule_id=row.rule_id, category=row.category, severity=row.severity, title=row.title,
            description=row.description, remediation=row.remediation, path=row.path, line=row.line,
            end_line=row.end_line, evidence=row.evidence, metadata=row.finding_metadata,
            verification=row.verification,
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
        explanation = ((run.report or {}).get("llm") or {}).get("text")
        if explanation:
            body += "\n## AI-рекомендации\n\n" + "\n".join("    " + line for line in explanation.splitlines()) + "\n"
    return Response(
        body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="report-{run.id}.{extension}"'},
    )
