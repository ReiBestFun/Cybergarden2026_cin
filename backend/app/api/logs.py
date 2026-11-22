from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db.models import Project, ScanRun, Workspace, WorkspaceLog, WorkspaceLogPolicy
from ..db.session import Database
from ..db.workspaces import project_access
from ..schemas.contracts import Page, ReadModel
from ..workspace_logs import LogPolicy, LogRecord, audit_action, policy_for
from .auth import CurrentUser
from .common import ApiError, page
from .workspaces import workspace_role

router = APIRouter(prefix="/workspaces/{workspace_id}/logs", tags=["workspace logs"])
Kind = Literal["audit", "diagnostic"]


class Permissions(ReadModel):
    view: bool
    export: bool
    manage: bool
    grant: bool


class PolicyRead(ReadModel):
    policy: LogPolicy
    permissions: Permissions
    destinations: list[str]


def _settings(db: Session, workspace_id: UUID, user_id: UUID, request: Request) -> PolicyRead:
    role = workspace_role(db, workspace_id, user_id)
    policy = policy_for(db, workspace_id)
    return PolicyRead(policy=policy, permissions=Permissions(
        view=role == "owner" or policy.member_view,
        export=role == "owner" or policy.member_export,
        manage=role == "owner" or policy.member_manage,
        grant=role == "owner",
    ), destinations=["postgresql", *sorted(request.app.state.settings.log_destinations)])


def _require(allowed: bool) -> None:
    if not allowed:
        raise ApiError(403, "LOG_ACCESS_DENIED", "Недостаточно прав для этого действия с журналом.")


@router.get("/policy")
def get_policy(workspace_id: UUID, user: CurrentUser, db: Database, request: Request) -> PolicyRead:
    return _settings(db, workspace_id, user.id, request)


@router.put("/policy")
def set_policy(workspace_id: UUID, data: LogPolicy, user: CurrentUser, db: Database, request: Request) -> PolicyRead:
    audit_action(db, workspace_id, user.id, "logs.policy")
    db.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    settings = _settings(db, workspace_id, user.id, request)
    _require(settings.permissions.manage)
    rights = {"member_view", "member_export", "member_manage"}
    if data.model_dump(include=rights) != settings.policy.model_dump(include=rights):
        _require(settings.permissions.grant)
    if data.destination not in settings.destinations:
        raise ApiError(422, "UNKNOWN_LOG_DESTINATION", "Выберите подключённое оператором хранилище.")
    db.merge(WorkspaceLogPolicy(workspace_id=workspace_id, **data.model_dump()))
    db.flush()
    db.commit()
    return _settings(db, workspace_id, user.id, request)


def _records(db: Session, workspace_id: UUID, user_id: UUID, kind: Kind | None, run_id: UUID | None):
    visible_runs = select(ScanRun.id).join(Project, Project.id == ScanRun.project_id).where(
        Project.workspace_id == workspace_id, project_access(user_id),
    )
    query = select(WorkspaceLog).where(
        WorkspaceLog.workspace_id == workspace_id,
        or_(WorkspaceLog.kind == "audit", WorkspaceLog.run_id.in_(visible_runs)),
    )
    if run_id is not None:
        if db.scalar(visible_runs.where(ScanRun.id == run_id)) is None:
            raise ApiError(404, "RUN_NOT_FOUND", "Запуск не найден.")
        query = query.where(WorkspaceLog.run_id == run_id)
    if kind:
        query = query.where(WorkspaceLog.kind == kind)
    return query.order_by(WorkspaceLog.at.desc(), WorkspaceLog.id.desc())


@router.get("", response_model=Page[LogRecord])
def list_logs(workspace_id: UUID, user: CurrentUser, db: Database, request: Request,
              kind: Kind | None = None, run_id: UUID | None = None,
              limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    _require(_settings(db, workspace_id, user.id, request).permissions.view)
    return page(db, _records(db, workspace_id, user.id, kind, run_id), limit, offset)


@router.get("/export", response_model=list[LogRecord])
def export_logs(workspace_id: UUID, user: CurrentUser, db: Database, request: Request,
                kind: Kind | None = None, run_id: UUID | None = None):
    audit_action(db, workspace_id, user.id, "logs.export")
    _require(_settings(db, workspace_id, user.id, request).permissions.export)
    records = [LogRecord.model_validate(row) for row in db.scalars(_records(db, workspace_id, user.id, kind, run_id))]
    db.commit()
    return records
