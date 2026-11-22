from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db.models import NodeRun, Project, ScanRun, Schedule, Workflow
from ..schemas.contracts import Graph, NodeStatus
from ..workspace_logs import diagnostic
from .schedules import advance
from .validation import validate_graph


def enqueue_manual_run(db: Session, project: Project, workflow: Workflow, graph: Graph, ref: str) -> ScanRun:
    return _enqueue(db, project, workflow, graph, ref, trigger="manual")


def enqueue_ci_run(
    db: Session, project: Project, workflow: Workflow, graph: Graph, sha: str, request_id: str,
) -> ScanRun:
    return _enqueue(db, project, workflow, graph, sha, trigger="ci", ci_request_id=request_id)


def _enqueue(
    db: Session, project: Project, workflow: Workflow, graph: Graph, ref: str, *,
    trigger: str, schedule: Schedule | None = None, scheduled_for: datetime | None = None,
    ci_request_id: str | None = None,
) -> ScanRun:
    run = ScanRun(
        project_id=project.id,
        workflow_id=workflow.id,
        trigger=trigger,
        status="queued",
        graph_snapshot=graph.model_dump(mode="json"),
        source_snapshot={"repository_url": project.repository_url, "ref": ref},
        ci_request_id=ci_request_id,
        schedule_id=schedule.id if schedule else None,
        scheduled_for=scheduled_for,
    )
    db.add(run)
    db.flush()
    diagnostic(db, run, "run.enqueue", "queued")
    db.add_all([
        NodeRun(run_id=run.id, node_id=node.id, type=node.type, status="pending")
        for node in graph.nodes
    ])
    return run


@dataclass(frozen=True)
class ClaimedRun:
    id: UUID
    project_id: UUID
    token: UUID
    trigger: str
    graph: dict
    source: dict


def claim_next(db: Session) -> ClaimedRun | None:
    run = db.scalar(
        select(ScanRun).join(Project, Project.id == ScanRun.project_id)
        .where(ScanRun.status == "queued", Project.owner_id.is_not(None))
        .order_by(ScanRun.created_at, ScanRun.id).limit(1).with_for_update(of=ScanRun, skip_locked=True)
    )
    if run is None:
        return None
    now = datetime.now(UTC)
    token = uuid4()
    run.status = "running"
    run.claim_token = token
    run.started_at = now
    run.heartbeat_at = now
    diagnostic(db, run, "run.claim", "running")
    return ClaimedRun(run.id, run.project_id, token, run.trigger, run.graph_snapshot, run.source_snapshot)


@dataclass(frozen=True)
class FailedRun:
    id: UUID
    nodes: list[tuple[UUID, str, NodeStatus]]


def fail_run(db: Session, run: ScanRun, code: str, message: str) -> FailedRun:
    now = datetime.now(UTC)
    error = {"code": code, "message": message}
    run.status = "failed"
    run.error = error
    run.finished_at = now
    run.claim_token = None
    transitions: tuple[tuple[NodeStatus, NodeStatus], ...] = (("running", "failed"), ("pending", "skipped"))
    nodes: list[tuple[UUID, str, NodeStatus]] = []
    for previous, status in transitions:
        rows = db.execute(
            update(NodeRun).where(NodeRun.run_id == run.id, NodeRun.status == previous)
            .values(status=status, finished_at=now, error=error).returning(NodeRun.id, NodeRun.type)
        )
        nodes.extend((node_id, node_type, status) for node_id, node_type in rows)
    diagnostic(db, run, "run.finish", "failed")
    for node_id, _, status in nodes:
        diagnostic(db, run, "node.finish", status, node_id)
    return FailedRun(run.id, nodes)


def expire_stale_runs(db: Session, cutoff: datetime) -> list[FailedRun]:
    runs = db.scalars(
        select(ScanRun).join(Project, Project.id == ScanRun.project_id).where(
            ScanRun.status == "running", Project.owner_id.is_not(None),
            func.coalesce(ScanRun.heartbeat_at, ScanRun.started_at, ScanRun.created_at) < cutoff,
        ).with_for_update(of=ScanRun, skip_locked=True)
    ).all()
    return [fail_run(db, run, "WORKER_EXPIRED", "Воркер перестал обновлять состояние запуска.") for run in runs]


def enqueue_due_schedules(db: Session, now: datetime, limit: int = 20) -> int:
    """Create runs for schedules whose slot has come.

    Rows are locked with SKIP LOCKED and runs are unique per (schedule, slot), so several
    workers can tick at once without duplicating runs. Missed slots (the worker was down)
    produce a single run, after which the schedule moves to the next future slot.
    """
    schedules = db.scalars(
        select(Schedule).join(Workflow, Workflow.id == Schedule.workflow_id).join(Project, Project.id == Workflow.project_id)
        .where(Schedule.enabled, Schedule.next_run_at <= now, Project.owner_id.is_not(None))
        .order_by(Schedule.next_run_at).limit(limit).with_for_update(of=Schedule, skip_locked=True)
    ).all()
    created = 0
    for schedule in schedules:
        slot = schedule.next_run_at
        workflow = db.get(Workflow, schedule.workflow_id)
        project = db.get(Project, workflow.project_id) if workflow else None
        graph = Graph.model_validate(workflow.graph_json) if workflow else None
        issues = validate_graph(graph, expected_trigger="schedule_trigger") if graph else []
        if workflow is None or project is None or graph is None:
            schedule.last_error = {"code": "WORKFLOW_NOT_FOUND", "message": "Сценарий расписания не найден."}
        elif issues:
            schedule.last_error = issues[0].model_dump(exclude={"edge_id"})
        else:
            already = db.scalar(select(func.count()).select_from(ScanRun).where(
                ScanRun.schedule_id == schedule.id, ScanRun.scheduled_for == slot,
            ))
            if not already:
                _enqueue(db, project, workflow, graph, project.default_ref,
                         trigger="schedule", schedule=schedule, scheduled_for=slot)
                created += 1
            schedule.last_enqueued_at = now
            schedule.last_error = None
        advance(schedule, now)
    return created
