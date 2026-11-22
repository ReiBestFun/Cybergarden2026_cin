"""Structured workspace events. Only server-generated metadata enters the journal."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .db.models import Project, ScanRun, WorkspaceLog, WorkspaceLogPolicy, WorkspaceMembership
from .schemas.contracts import Input, ReadModel


class LogPolicy(Input):
    destination: str = Field(default="postgresql", min_length=1, max_length=48)
    audit_days: int = Field(default=90, ge=1, le=3650)
    diagnostic_days: int = Field(default=14, ge=1, le=3650)
    member_view: bool = False
    member_export: bool = False
    member_manage: bool = False


class LogRecord(ReadModel):
    id: UUID
    workspace_id: UUID
    kind: Literal["audit", "diagnostic"]
    at: datetime
    action: str
    result: str
    actor_id: UUID | None
    run_id: UUID | None
    node_run_id: UUID | None


def policy_for(db: Session, workspace_id: UUID) -> LogPolicy:
    row = db.get(WorkspaceLogPolicy, workspace_id)
    return LogPolicy.model_validate(row, from_attributes=True) if row else LogPolicy()


def _audit(db: Session, workspace_id: UUID, actor_id: UUID, action: str, result: str) -> None:
    db.add(WorkspaceLog(workspace_id=workspace_id, kind="audit", actor_id=actor_id,
                        action=action, result=result, destination=policy_for(db, workspace_id).destination))


def audit_action(db: Session, workspace_id: UUID, actor_id: UUID, action: str) -> None:
    db.info["audit_action"] = (workspace_id, actor_id, action)

    def before_commit(session: Session) -> None:
        context = session.info.pop("audit_action", None)
        if context:
            _audit(session, *context, "succeeded")

    event.listen(db, "before_commit", before_commit, once=True)


def rejected_action(db: Session, status: int) -> None:
    context = db.info.pop("audit_action", None)
    if context:
        db.rollback()
        workspace_id, actor_id, action = context
        if db.get(WorkspaceMembership, (workspace_id, actor_id)):
            _audit(db, workspace_id, actor_id, action, "failed" if status >= 500 else "denied")
            db.commit()


def diagnostic(db: Session, run: ScanRun, action: str, result: str, node_run_id: UUID | None = None) -> None:
    workspace_id = db.scalar(select(Project.workspace_id).where(Project.id == run.project_id))
    if workspace_id is not None:
        db.add(WorkspaceLog(workspace_id=workspace_id, kind="diagnostic", action=action, result=result,
                            run_id=run.id, node_run_id=node_run_id, destination=policy_for(db, workspace_id).destination))
