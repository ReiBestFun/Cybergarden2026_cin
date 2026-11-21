from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from ..db.models import Project
from ..db.session import Database
from ..integrations.repository import is_repository_allowed
from ..schemas.contracts import Page, ProjectCreate, ProjectRead
from .common import ApiError, PageQuery, normalize_repository, page

router = APIRouter(tags=["projects"])


def get_project(project_id: UUID, db: Database) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise ApiError(404, "PROJECT_NOT_FOUND", "Проект не найден.")
    return project


@router.get("/projects", response_model=Page[ProjectRead])
def list_projects(db: Database, pagination: PageQuery) -> dict:
    statement = select(Project).order_by(Project.created_at.desc(), Project.id.desc())
    return page(db, statement, pagination.limit, pagination.offset)


@router.post("/projects", status_code=201, response_model=ProjectRead)
def create_project(data: ProjectCreate, request: Request, db: Database) -> Project:
    url = normalize_repository(data.repository_url)
    if not is_repository_allowed(url, request.app.state.allowed_repositories):
        raise ApiError(422, "REPOSITORY_NOT_ALLOWED", "Репозиторий отсутствует в списке учебных источников.", field="repository_url")
    project = Project(name=data.name, repository_url=url, default_ref=data.default_ref)
    db.add(project)
    db.commit()
    return project


@router.get("/projects/{project_id}", response_model=ProjectRead)
def project_detail(project_id: UUID, db: Database) -> Project:
    return get_project(project_id, db)
