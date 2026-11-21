from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db.encryption import require_cipher
from ..db.models import Project
from ..db.session import Database
from ..integrations.repository import is_repository_allowed
from ..schemas.contracts import Page, ProjectCreate, ProjectPatch, ProjectRead
from .auth import CurrentUser
from .common import ApiError, PageQuery, normalize_repository, page

router = APIRouter(tags=["projects"])


def get_project(project_id: UUID, db: Database, owner_id: UUID) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id))
    if project is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", "Проект не найден.")
    return project


@router.get("/projects", response_model=Page[ProjectRead])
def list_projects(db: Database, pagination: PageQuery, user: CurrentUser) -> dict:
    statement = select(Project).where(Project.owner_id == user.id).order_by(Project.created_at.desc(), Project.id.desc())
    return page(db, statement, pagination.limit, pagination.offset)


@router.post("/projects", status_code=201, response_model=ProjectRead, responses={200: {"model": ProjectRead}})
def create_project(data: ProjectCreate, request: Request, response: Response, db: Database, user: CurrentUser) -> Project:
    url = normalize_repository(data.repository_url)
    if not is_repository_allowed(url, request.app.state.allowed_repositories):
        raise ApiError(422, "REPOSITORY_NOT_ALLOWED", "Репозиторий отсутствует в списке учебных источников.", field="repository_url")
    key = require_cipher(db.get_bind().dialect).digest(f"project:{user.id}", url.casefold())
    project = db.scalar(insert(Project).values(
        owner_id=user.id, repository_key=key, name=data.name, repository_url=url, default_ref=data.default_ref,
    ).on_conflict_do_nothing(constraint="uq_project_owner_repository").returning(Project))
    if project is None:
        project = db.scalars(select(Project).where(Project.owner_id == user.id, Project.repository_key == key)).one()
        response.status_code = 200
    db.commit()
    return project


@router.get("/projects/{project_id}", response_model=ProjectRead)
def project_detail(project_id: UUID, db: Database, user: CurrentUser) -> Project:
    return get_project(project_id, db, user.id)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(project_id: UUID, data: ProjectPatch, db: Database, user: CurrentUser) -> Project:
    project = get_project(project_id, db, user.id)
    if data.name is not None:
        project.name = data.name
    if data.default_ref is not None:
        project.default_ref = data.default_ref
    db.commit()
    return project
