from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db.models import NodeRun, Project, ScanRun, Workflow
from ..schemas.contracts import Graph


def enqueue_manual_run(db: Session, project: Project, workflow: Workflow, graph: Graph, ref: str) -> ScanRun:
    run = ScanRun(
        project_id=project.id,
        workflow_id=workflow.id,
        trigger="manual",
        status="queued",
        graph_snapshot=graph.model_dump(mode="json"),
        source_snapshot={"repository_url": project.repository_url, "ref": ref},
    )
    db.add(run)
    db.flush()
    db.add_all([
        NodeRun(run_id=run.id, node_id=node.id, type=node.type, status="pending")
        for node in graph.nodes
    ])
    return run


@dataclass(frozen=True)
class ClaimedRun:
    id: UUID
    token: UUID
    trigger: str
    graph: dict
    source: dict


def claim_next(db: Session) -> ClaimedRun | None:
    run = db.scalar(
        select(ScanRun).where(ScanRun.status == "queued")
        .order_by(ScanRun.created_at, ScanRun.id).limit(1).with_for_update(skip_locked=True)
    )
    if run is None:
        return None
    now = datetime.now(UTC)
    token = uuid4()
    run.status = "running"
    run.claim_token = token
    run.started_at = now
    run.heartbeat_at = now
    return ClaimedRun(run.id, token, run.trigger, run.graph_snapshot, run.source_snapshot)


def fail_run(db: Session, run: ScanRun, code: str, message: str) -> None:
    now = datetime.now(UTC)
    error = {"code": code, "message": message}
    run.status = "failed"
    run.error = error
    run.finished_at = now
    run.claim_token = None
    db.execute(update(NodeRun).where(
        NodeRun.run_id == run.id, NodeRun.status == "running",
    ).values(status="failed", finished_at=now, error=error))
    db.execute(update(NodeRun).where(
        NodeRun.run_id == run.id, NodeRun.status == "pending",
    ).values(status="skipped", finished_at=now, error=error))


def expire_stale_runs(db: Session, cutoff: datetime) -> int:
    runs = db.scalars(
        select(ScanRun).where(
            ScanRun.status == "running",
            func.coalesce(ScanRun.heartbeat_at, ScanRun.started_at, ScanRun.created_at) < cutoff,
        ).with_for_update(skip_locked=True)
    ).all()
    for run in runs:
        fail_run(db, run, "WORKER_EXPIRED", "Воркер перестал обновлять состояние запуска.")
    return len(runs)
