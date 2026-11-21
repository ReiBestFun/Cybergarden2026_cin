import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from ..db.models import Project, Schedule, Workflow
from ..db.session import Database
from ..db.workspaces import project_access
from ..integrations.repository import is_repository_allowed
from ..llm import ModelError
from ..nodes.catalog import CATALOG, CatalogRead
from ..schemas.contracts import (
    Graph, Page, SchedulePreview, ScheduleRead, ScheduleSettings, ScheduleUpdate, ValidateWorkflow, ValidationResult,
    WorkflowCreate, WorkflowPatch, WorkflowRead,
)
from ..workflows.generation import GeneratedWorkflow, GenerationError, GenerationRequest, generate_workflow
from ..workflows.scheduling import ScheduleSpecError, next_slot
from ..workflows.schedules import apply_spec, preview, schedule_read, spec_from_settings
from ..workflows.validation import validate_draft, validate_graph
from .auth import CurrentUser
from .common import ApiError, PageQuery, graph_error, normalize_repository, page
from .projects import get_project

router = APIRouter(tags=["workflows"])


def get_workflow(workflow_id: UUID, db: Database, user_id: UUID, *, lock: bool = False, owner_required: bool = False) -> Workflow:
    statement = select(Workflow).join(Project, Project.id == Workflow.project_id).where(
        Workflow.id == workflow_id, project_access(user_id),
    )
    workflow = db.scalar(statement.with_for_update(of=Workflow) if lock else statement)
    if workflow is None:
        raise ApiError(404, "WORKFLOW_NOT_FOUND", "Сценарий не найден.")
    if owner_required:
        get_project(workflow.project_id, db, user_id, owner_required=True)
    return workflow


def checked_draft(graph: Graph) -> dict:
    if issues := validate_draft(graph):
        raise graph_error(issues[0])
    return graph.model_dump(mode="json")


@router.get("/check-types", response_model=CatalogRead)
def list_node_types(user: CurrentUser) -> CatalogRead:
    return CatalogRead(items=list(CATALOG.values()))


@router.post("/workflows/generate", response_model=GeneratedWorkflow)
def generate_draft(data: GenerationRequest, request: Request, user: CurrentUser) -> GeneratedWorkflow:
    urls = {normalize_repository(url.rstrip(".,;:!?")) for url in re.findall(
        r"""https://github\.com/[^\s<>()\[\]{}"'\x60]+""", data.description,
    )}
    if len(urls) > 1:
        raise ApiError(422, "MULTIPLE_REPOSITORIES", "Укажите один учебный репозиторий для сценария.")
    repository_url = next(iter(urls), None)
    if repository_url is not None and not is_repository_allowed(repository_url, request.app.state.allowed_repositories):
        raise ApiError(422, "REPOSITORY_NOT_ALLOWED", "Репозиторий отсутствует в списке учебных источников.")
    try:
        return generate_workflow(data, request.app.state.settings, repository_url)
    except ModelError as error:
        raise ApiError(502, "MODEL_ERROR", str(error)) from None
    except GenerationError as error:
        raise ApiError(422, "GENERATION_FAILED", str(error)) from None


@router.get("/projects/{project_id}/workflows", response_model=Page[WorkflowRead])
def list_workflows(project_id: UUID, db: Database, pagination: PageQuery, user: CurrentUser) -> dict:
    get_project(project_id, db, user.id)
    statement = select(Workflow).where(Workflow.project_id == project_id).order_by(Workflow.updated_at.desc(), Workflow.id.desc())
    return page(db, statement, pagination.limit, pagination.offset)


@router.post("/projects/{project_id}/workflows", status_code=201, response_model=WorkflowRead)
def create_workflow(project_id: UUID, data: WorkflowCreate, db: Database, user: CurrentUser) -> Workflow:
    get_project(project_id, db, user.id, owner_required=True)
    workflow = Workflow(project_id=project_id, name=data.name, graph_json=checked_draft(data.graph))
    db.add(workflow)
    db.commit()
    return workflow


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
def workflow_detail(workflow_id: UUID, db: Database, user: CurrentUser) -> Workflow:
    return get_workflow(workflow_id, db, user.id)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowRead)
def modify_workflow(workflow_id: UUID, data: WorkflowPatch, db: Database, user: CurrentUser) -> Workflow:
    workflow = get_workflow(workflow_id, db, user.id, lock=True, owner_required=True)
    if data.name is not None:
        workflow.name = data.name
    if data.graph is not None:
        workflow.graph_json = checked_draft(data.graph)
        schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow_id).with_for_update())
        if schedule is not None and schedule.enabled:
            issues = validate_graph(data.graph, expected_trigger="schedule_trigger")
            if issues:
                schedule.enabled = False
                schedule.next_run_at = None
                schedule.last_error = issues[0].model_dump(exclude={"edge_id"})
    db.commit()
    return workflow


@router.post("/workflows/{workflow_id}/validate", response_model=ValidationResult)
def validate_workflow(workflow_id: UUID, db: Database, user: CurrentUser, data: ValidateWorkflow | None = None) -> ValidationResult:
    workflow = get_workflow(workflow_id, db, user.id)
    graph = data.graph if data and data.graph is not None else Graph.model_validate(workflow.graph_json)
    issues = validate_graph(graph)
    return ValidationResult(valid=not issues, issues=issues)


@router.get("/workflows/{workflow_id}/schedule", response_model=ScheduleRead)
def get_schedule(workflow_id: UUID, db: Database, user: CurrentUser) -> ScheduleRead:
    get_workflow(workflow_id, db, user.id)
    schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow_id))
    return schedule_read(schedule)


def _spec(data: ScheduleSettings):
    try:
        return spec_from_settings(data)
    except ScheduleSpecError as error:
        raise ApiError(422, error.code, error.message, field=error.field) from None


@router.post("/schedules/preview", response_model=SchedulePreview)
def preview_schedule(data: ScheduleSettings, user: CurrentUser) -> SchedulePreview:
    if data.timezone is None:
        data = data.model_copy(update={"timezone": user.timezone})
    _spec(data)
    return SchedulePreview(next_runs=preview(data))


@router.put("/workflows/{workflow_id}/schedule", response_model=ScheduleRead)
def set_schedule(workflow_id: UUID, data: ScheduleUpdate, db: Database, user: CurrentUser) -> ScheduleRead:
    workflow = get_workflow(workflow_id, db, user.id, lock=True, owner_required=True)
    schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow_id).with_for_update())
    if not data.enabled:
        if data.timezone is not None:
            spec = _spec(data)
            if schedule is None:
                schedule = Schedule(workflow_id=workflow_id)
                db.add(schedule)
            apply_spec(schedule, spec)
        if schedule is not None:
            schedule.enabled = False
            schedule.next_run_at = None
            if data.timezone is not None:
                schedule.last_error = None
            db.commit()
        return schedule_read(schedule)

    if data.timezone is None:
        data = data.model_copy(update={"timezone": user.timezone})
    spec = _spec(data)
    graph = Graph.model_validate(workflow.graph_json)
    if issues := validate_graph(graph, expected_trigger="schedule_trigger"):
        raise graph_error(issues[0])
    if schedule is None:
        schedule = Schedule(workflow_id=workflow_id)
        db.add(schedule)
    changed = apply_spec(schedule, spec)
    if changed or not schedule.enabled or schedule.next_run_at is None:
        schedule.next_run_at = next_slot(spec, datetime.now(UTC))
    schedule.enabled = True
    schedule.last_error = None
    db.commit()
    return schedule_read(schedule)
