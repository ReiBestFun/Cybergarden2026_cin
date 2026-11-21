from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from ..db.models import Schedule, Workflow
from ..db.session import Database
from ..nodes.catalog import CATALOG, CatalogRead
from ..schemas.contracts import (
    Graph, Page, SchedulePreview, ScheduleRead, ScheduleSettings, ScheduleUpdate, ValidateWorkflow, ValidationResult,
    WorkflowCreate, WorkflowPatch, WorkflowRead,
)
from ..workflows.scheduling import ScheduleSpecError, next_slot
from ..workflows.schedules import apply_spec, preview, schedule_read, spec_from_settings
from ..workflows.validation import validate_draft, validate_graph
from .common import ApiError, PageQuery, graph_error, page
from .projects import get_project

router = APIRouter(tags=["workflows"])


def get_workflow(workflow_id: UUID, db: Database, *, lock: bool = False) -> Workflow:
    statement = select(Workflow).where(Workflow.id == workflow_id)
    workflow = db.scalar(statement.with_for_update() if lock else statement)
    if workflow is None:
        raise ApiError(404, "WORKFLOW_NOT_FOUND", "Сценарий не найден.")
    return workflow


def _draft(graph: Graph) -> dict:
    if issues := validate_draft(graph):
        raise graph_error(issues[0])
    return graph.model_dump(mode="json")


@router.get("/check-types", response_model=CatalogRead)
def list_node_types() -> CatalogRead:
    return CatalogRead(items=list(CATALOG.values()))


@router.get("/projects/{project_id}/workflows", response_model=Page[WorkflowRead])
def list_workflows(project_id: UUID, db: Database, pagination: PageQuery) -> dict:
    get_project(project_id, db)
    statement = select(Workflow).where(Workflow.project_id == project_id).order_by(Workflow.updated_at.desc(), Workflow.id.desc())
    return page(db, statement, pagination.limit, pagination.offset)


@router.post("/projects/{project_id}/workflows", status_code=201, response_model=WorkflowRead)
def create_workflow(project_id: UUID, data: WorkflowCreate, db: Database) -> Workflow:
    get_project(project_id, db)
    workflow = Workflow(project_id=project_id, name=data.name, graph_json=_draft(data.graph))
    db.add(workflow)
    db.commit()
    return workflow


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
def workflow_detail(workflow_id: UUID, db: Database) -> Workflow:
    return get_workflow(workflow_id, db)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowRead)
def modify_workflow(workflow_id: UUID, data: WorkflowPatch, db: Database) -> Workflow:
    workflow = get_workflow(workflow_id, db, lock=True)
    if data.name is not None:
        workflow.name = data.name
    if data.graph is not None:
        workflow.graph_json = _draft(data.graph)
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
def validate_workflow(workflow_id: UUID, db: Database, data: ValidateWorkflow | None = None) -> ValidationResult:
    workflow = get_workflow(workflow_id, db)
    graph = data.graph if data and data.graph is not None else Graph.model_validate(workflow.graph_json)
    issues = validate_graph(graph)
    return ValidationResult(valid=not issues, issues=issues)


@router.get("/workflows/{workflow_id}/schedule", response_model=ScheduleRead)
def get_schedule(workflow_id: UUID, db: Database) -> ScheduleRead:
    get_workflow(workflow_id, db)
    schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow_id))
    return schedule_read(schedule)


def _spec(data: ScheduleSettings):
    try:
        return spec_from_settings(data)
    except ScheduleSpecError as error:
        raise ApiError(422, error.code, error.message, field=error.field) from None


@router.post("/schedules/preview", response_model=SchedulePreview)
def preview_schedule(data: ScheduleSettings) -> SchedulePreview:
    _spec(data)
    return SchedulePreview(next_runs=preview(data))


@router.put("/workflows/{workflow_id}/schedule", response_model=ScheduleRead)
def set_schedule(workflow_id: UUID, data: ScheduleUpdate, db: Database) -> ScheduleRead:
    workflow = get_workflow(workflow_id, db, lock=True)
    schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow_id).with_for_update())
    if not data.enabled:
        if schedule is not None:
            schedule.enabled = False
            schedule.next_run_at = None
            db.commit()
        return schedule_read(schedule)

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
