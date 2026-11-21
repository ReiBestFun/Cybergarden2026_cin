from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .encryption import require_cipher
from .models import Project, Workspace, WorkspaceMembership

PERSONAL_WORKSPACE_NAME = "Личное пространство"


def workspace_repository_key(db: Session, workspace_id: UUID, repository_url: str) -> str:
    return require_cipher(db.get_bind().dialect).digest(f"project:{workspace_id}", repository_url.casefold())


def provision_personal_workspace(db: Session, user_id: UUID) -> None:
    db.execute(insert(Workspace).values(
        id=user_id, name=PERSONAL_WORKSPACE_NAME, personal_user_id=user_id,
    ).on_conflict_do_nothing(index_elements=[Workspace.id]))
    db.execute(insert(WorkspaceMembership).values(
        workspace_id=user_id, user_id=user_id, role="owner",
    ).on_conflict_do_nothing(index_elements=[WorkspaceMembership.workspace_id, WorkspaceMembership.user_id]))


def project_access(user_id: UUID):
    return select(WorkspaceMembership.workspace_id).where(
        WorkspaceMembership.workspace_id == Project.workspace_id,
        WorkspaceMembership.user_id == user_id,
    ).exists()
