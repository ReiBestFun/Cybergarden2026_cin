from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from ..db.models import Project, Workspace
from ..db.session import Database
from ..db.workspaces import project_access, workspace_repository_key
from ..integrations.repository import is_repository_allowed
from ..schemas.contracts import Page, ProjectCreate, ProjectMove, ProjectPatch, ProjectRead
from .auth import CurrentUser
from .common import ApiError, PageQuery, normalize_repository, page
from .workspaces import require_workspace_owner, workspace_role

router = APIRouter(tags=["projects"])


def get_project(project_id: UUID, db: Database, user_id: UUID, *, lock: bool = False, owner_required: bool = False) -> Project:
    statement = select(Project).where(Project.id == project_id, project_access(user_id))
    project = db.scalar(statement.with_for_update(of=Project) if lock else statement)
    if project is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", "Проект не найден.")
    if owner_required:
        require_workspace_owner(db, project.workspace_id, user_id)
    return project


@router.get("/projects", response_model=Page[ProjectRead])
def list_projects(db: Database, pagination: PageQuery, user: CurrentUser, workspace_id: UUID | None = None) -> dict:
    workspace_id = workspace_id or user.id
    workspace_role(db, workspace_id, user.id)
    statement = select(Project).where(
        Project.workspace_id == workspace_id, project_access(user.id),
    ).order_by(Project.created_at.desc(), Project.id.desc())
    return page(db, statement, pagination.limit, pagination.offset)


@router.post("/projects", status_code=201, response_model=ProjectRead, responses={200: {"model": ProjectRead}})
def create_project(data: ProjectCreate, request: Request, response: Response, db: Database, user: CurrentUser) -> Project:
    workspace_id = data.workspace_id or user.id
    require_workspace_owner(db, workspace_id, user.id)
    url = normalize_repository(data.repository_url)
    if not is_repository_allowed(url, request.app.state.allowed_repositories):
        raise ApiError(422, "REPOSITORY_NOT_ALLOWED", "Репозиторий отсутствует в списке учебных источников.", field="repository_url")
    key = workspace_repository_key(db, workspace_id, url)
    statement = insert(Project).values(
        owner_id=user.id, workspace_id=workspace_id, repository_key=key,
        name=data.name, repository_url=url, default_ref=data.default_ref,
    ).on_conflict_do_nothing(constraint="uq_project_workspace_repository").returning(Project)
    for _ in range(2):
        project = db.scalar(statement)
        if project is not None:
            db.commit()
            return project
        # The conflicting project can move out of the workspace before this lookup.
        project = db.scalar(select(Project).where(
            Project.workspace_id == workspace_id, Project.repository_key == key,
        ).with_for_update(of=Project))
        if project is not None:
            response.status_code = 200
            db.commit()
            return project
    raise ApiError(409, "PROJECT_CHANGED_RETRY", "Список проектов изменился. Повторите добавление.")


@router.post("/projects/{project_id}/move", response_model=ProjectRead)
def move_project(project_id: UUID, data: ProjectMove, db: Database, user: CurrentUser) -> Project:
    project = get_project(project_id, db, user.id, lock=True, owner_required=True)
    require_workspace_owner(db, data.workspace_id, user.id)
    if db.get(Workspace, data.workspace_id).personal:
        raise ApiError(409, "TEAM_WORKSPACE_REQUIRED", "Выберите командное рабочее пространство.")
    if project.workspace_id == data.workspace_id:
        return project
    if project.workspace_id != user.id:
        raise ApiError(409, "PROJECT_NOT_PERSONAL", "Перенести можно проект из личного пространства.")

    project.workspace_id = data.workspace_id
    project.repository_key = workspace_repository_key(db, data.workspace_id, project.repository_url)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if getattr(getattr(error.orig, "diag", None), "constraint_name", None) != "uq_project_workspace_repository":
            raise
        raise ApiError(409, "WORKSPACE_REPOSITORY_EXISTS", "В этом пространстве уже есть такой репозиторий.") from None
    return project


@router.get("/projects/{project_id}", response_model=ProjectRead)
def project_detail(project_id: UUID, db: Database, user: CurrentUser) -> Project:
    return get_project(project_id, db, user.id)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(project_id: UUID, data: ProjectPatch, db: Database, user: CurrentUser) -> Project:
    project = get_project(project_id, db, user.id, owner_required=True)
    if data.name is not None:
        project.name = data.name
    if data.default_ref is not None:
        project.default_ref = data.default_ref
    db.commit()
    return project
