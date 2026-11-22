from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db.models import User, Workspace, WorkspaceInvitation, WorkspaceMembership
from ..db.session import Database
from ..workspace_logs import audit_action
from ..schemas.contracts import Input, Page, ReadModel, WorkspaceCreate, WorkspaceRead, WorkspaceRole
from .auth import CurrentUser, token_hash
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


class MemberRole(Input):
    role: WorkspaceRole


class MemberRead(ReadModel):
    id: UUID
    github_login: str
    role: WorkspaceRole


class InviteCreate(MemberRole):
    days: int = Field(default=7, ge=1, le=30)


class InviteRead(ReadModel):
    id: UUID
    role: WorkspaceRole
    expires_at: datetime


class InviteIssued(InviteRead):
    token: str


class Redeem(Input):
    token: str = Field(min_length=43, max_length=43)


def _team(db: Session, workspace_id: UUID, *, lock: bool = False) -> Workspace:
    statement = select(Workspace).where(Workspace.id == workspace_id, Workspace.personal_user_id.is_(None))
    team = db.scalar(statement.with_for_update() if lock else statement)
    if team is None:
        raise ApiError(404, "WORKSPACE_NOT_FOUND", "Командное пространство не найдено.")
    return team


def require_team_owner(db: Session, workspace_id: UUID, user_id: UUID, *, lock: bool = True) -> None:
    # Serialize membership changes, including checking that a team keeps its last owner.
    _team(db, workspace_id, lock=lock)
    require_workspace_owner(db, workspace_id, user_id)


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def invite(workspace_id: UUID, data: InviteCreate, user: CurrentUser, db: Database) -> InviteIssued:
    audit_action(db, workspace_id, user.id, "invitation.issue")
    require_team_owner(db, workspace_id, user.id)
    token = token_urlsafe(32)
    row = WorkspaceInvitation(workspace_id=workspace_id, issuer_id=user.id, token_hash=token_hash(token), role=data.role,
                              expires_at=datetime.now(UTC) + timedelta(days=data.days))
    db.add(row)
    db.commit()
    return InviteIssued(id=row.id, role=row.role, expires_at=row.expires_at, token=token)


@router.get("/workspaces/{workspace_id}/invitations", response_model=list[InviteRead])
def invitations(workspace_id: UUID, user: CurrentUser, db: Database):
    require_team_owner(db, workspace_id, user.id, lock=False)
    return db.scalars(select(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == workspace_id, WorkspaceInvitation.expires_at > func.now(),
    ).order_by(WorkspaceInvitation.expires_at, WorkspaceInvitation.id)).all()


@router.delete("/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204)
def revoke(workspace_id: UUID, invitation_id: UUID, user: CurrentUser, db: Database) -> Response:
    audit_action(db, workspace_id, user.id, "invitation.revoke")
    require_team_owner(db, workspace_id, user.id)
    db.execute(delete(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == workspace_id, WorkspaceInvitation.id == invitation_id,
    ))
    db.commit()
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/join")
def join_team(workspace_id: UUID, data: Redeem, user: CurrentUser, db: Database) -> WorkspaceRead:
    audit_action(db, workspace_id, user.id, "invitation.redeem")
    team = _team(db, workspace_id, lock=True)
    role = db.scalar(delete(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == team.id, WorkspaceInvitation.token_hash == token_hash(data.token),
        WorkspaceInvitation.expires_at > func.clock_timestamp(),
    ).returning(WorkspaceInvitation.role))
    if role is None:
        raise ApiError(410, "INVITATION_UNAVAILABLE", "Ссылка использована, отозвана или срок её действия истёк.")
    if db.get(WorkspaceMembership, (team.id, user.id)):
        raise ApiError(409, "ALREADY_MEMBER", "Вы уже состоите в этом пространстве.")
    db.add(WorkspaceMembership(workspace_id=team.id, user_id=user.id, role=role))
    db.commit()
    return WorkspaceRead(id=team.id, name=team.name, personal=False, role=role)


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberRead])
def members(workspace_id: UUID, user: CurrentUser, db: Database):
    require_team_owner(db, workspace_id, user.id, lock=False)
    statement = select(User.id, User.github_login, WorkspaceMembership.role).join(
        WorkspaceMembership, WorkspaceMembership.user_id == User.id,
    ).where(WorkspaceMembership.workspace_id == workspace_id).order_by(User.id)
    return db.execute(statement).mappings().all()


def _change_member(db: Session, workspace_id: UUID, actor_id: UUID, member_id: UUID, role: str | None) -> None:
    require_team_owner(db, workspace_id, actor_id)
    member = db.get(WorkspaceMembership, (workspace_id, member_id))
    if member is None:
        raise ApiError(404, "MEMBER_NOT_FOUND", "Участник не найден.")
    if member.role == "owner" and role != "owner":
        owners = db.scalar(select(func.count()).select_from(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.role == "owner",
        ))
        if owners == 1:
            raise ApiError(409, "LAST_OWNER", "Сначала назначьте другого владельца.")
        db.execute(delete(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id, WorkspaceInvitation.issuer_id == member_id,
        ))
    if role is None:
        db.delete(member)
    else:
        member.role = role
    db.commit()


@router.patch("/workspaces/{workspace_id}/members/{member_id}")
def change_role(workspace_id: UUID, member_id: UUID, data: MemberRole, user: CurrentUser, db: Database) -> MemberRole:
    audit_action(db, workspace_id, user.id, "member.role")
    _change_member(db, workspace_id, user.id, member_id, data.role)
    return data


@router.delete("/workspaces/{workspace_id}/members/{member_id}", status_code=204)
def remove_member(workspace_id: UUID, member_id: UUID, user: CurrentUser, db: Database) -> Response:
    audit_action(db, workspace_id, user.id, "member.remove")
    _change_member(db, workspace_id, user.id, member_id, None)
    return Response(status_code=204)
