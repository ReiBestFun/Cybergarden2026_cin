from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Workspace, WorkspaceMembership
from ..db.session import Database
from ..schemas.contracts import Page, WorkspaceCreate, WorkspaceRead
from .auth import CurrentUser
from .common import ApiError, PageQuery

router = APIRouter(tags=["workspaces"])


def workspace_role(db: Session, workspace_id: UUID, user_id: UUID) -> str:
    role = db.scalar(select(WorkspaceMembership.role).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user_id,
    ))
    if role is None:
        raise ApiError(404, "WORKSPACE_NOT_FOUND", "Рабочее пространство не найдено.")
    return role


def require_workspace_owner(db: Session, workspace_id: UUID, user_id: UUID) -> None:
    if workspace_role(db, workspace_id, user_id) != "owner":
        raise ApiError(403, "WORKSPACE_OWNER_REQUIRED", "Это действие доступно владельцу рабочего пространства.")


@router.get("/workspaces", response_model=Page[WorkspaceRead])
def list_workspaces(db: Database, pagination: PageQuery, user: CurrentUser) -> dict:
    statement = select(
        Workspace.id, Workspace.name, Workspace.personal_user_id.is_not(None).label("personal"),
        WorkspaceMembership.role,
    ).join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id).where(
        WorkspaceMembership.user_id == user.id,
    ).order_by(Workspace.personal_user_id.is_not(None).desc(), Workspace.id)
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    items = db.execute(statement.limit(pagination.limit).offset(pagination.offset)).mappings().all()
    return {"items": items, "total": total, "limit": pagination.limit, "offset": pagination.offset}


@router.post("/workspaces", status_code=201, response_model=WorkspaceRead)
def create_workspace(data: WorkspaceCreate, db: Database, user: CurrentUser) -> WorkspaceRead:
    workspace = Workspace(name=data.name)
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.commit()
    return WorkspaceRead(id=workspace.id, name=workspace.name, personal=workspace.personal, role="owner")
