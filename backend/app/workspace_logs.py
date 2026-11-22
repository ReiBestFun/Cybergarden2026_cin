"""Structured workspace events. Only server-generated metadata enters the journal."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import case, event, func, select, text
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
    version: int = Field(default=0, ge=0)


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
    delivery_id: UUID | None


def policy_for(db: Session, workspace_id: UUID) -> LogPolicy:
    row = db.get(WorkspaceLogPolicy, workspace_id)
    return LogPolicy.model_validate(row, from_attributes=True) if row else LogPolicy()


def expires_at():
    defaults = LogPolicy()
    days = case((WorkspaceLog.kind == "audit", func.coalesce(WorkspaceLogPolicy.audit_days, defaults.audit_days)),
                else_=func.coalesce(WorkspaceLogPolicy.diagnostic_days, defaults.diagnostic_days))
    return WorkspaceLog.at + days * text("interval '1 day'")


@dataclass
class AuditAction:
    workspace_id: UUID
    actor_id: UUID
    action: str
    result: str = "succeeded"

    def record(self, db: Session) -> None:
        db.add(WorkspaceLog(workspace_id=self.workspace_id, kind="audit", actor_id=self.actor_id,
                            action=self.action, result=self.result, destination=policy_for(db, self.workspace_id).destination))


def audit_action(db: Session, workspace_id: UUID, actor_id: UUID, action: str) -> AuditAction:
    audit = AuditAction(workspace_id, actor_id, action)
    db.info["audit_action"] = audit

    def before_commit(session: Session) -> None:
        context = session.info.pop("audit_action", None)
        if context:
            context.record(session)

    event.listen(db, "before_commit", before_commit, once=True)
    return audit


def rejected_action(db: Session, status: int) -> None:
    context = db.info.pop("audit_action", None)
    if context:
        db.rollback()
        if db.get(WorkspaceMembership, (context.workspace_id, context.actor_id)):
            context.result = "failed" if status >= 500 else "denied"
            context.record(db)
            db.commit()


def diagnostic(db: Session, run_id: UUID, action: str, result: str, node_run_id: UUID | None = None,
               delivery_id: UUID | None = None) -> None:
    workspace_id = db.scalar(select(Project.workspace_id).join(ScanRun, ScanRun.project_id == Project.id)
                             .where(ScanRun.id == run_id))
    if workspace_id is not None:
        db.add(WorkspaceLog(workspace_id=workspace_id, kind="diagnostic", action=action, result=result,
                            run_id=run_id, node_run_id=node_run_id, delivery_id=delivery_id,
                            destination=policy_for(db, workspace_id).destination))
