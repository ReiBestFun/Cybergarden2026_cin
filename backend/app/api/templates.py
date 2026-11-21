from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from ..db.models import Schedule, Workflow, WorkflowTemplate
from ..db.session import Database
from ..nodes.catalog import NOTIFIER_CONFIGS
from ..schemas.contracts import Graph, Page, ScheduleSettings, WorkflowRead
from ..schemas.templates import TemplateCopy, TemplatePublish, TemplateRead
from ..workflows.schedules import apply_spec, schedule_read, spec_from_settings
from ..workflows.validation import validate_draft, validate_graph
from .common import ApiError, PageQuery, graph_error, page
from .projects import get_project
from .workflows import get_workflow

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=Page[TemplateRead])
def list_templates(db: Database, pagination: PageQuery) -> dict:
    statement = select(WorkflowTemplate).order_by(WorkflowTemplate.created_at.desc(), WorkflowTemplate.id.desc())
    return page(db, statement, pagination.limit, pagination.offset)


@router.post("", response_model=TemplateRead, status_code=201)
def publish_template(data: TemplatePublish, db: Database) -> WorkflowTemplate:
    workflow = get_workflow(data.workflow_id, db, lock=True)
    graph = Graph.model_validate(workflow.graph_json)
    if issues := validate_graph(graph):
        raise graph_error(issues[0])
    schedule_config = None
    for node in graph.nodes:
        node.config.pop("credential_id", None)
        if node.type in NOTIFIER_CONFIGS:
            for field in ("recipients", "subject"):
                node.config.pop(field, None)
        if node.type == "schedule_trigger":
            schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow.id))
            if schedule is not None and schedule.timezone:
                data_fields = schedule_read(schedule).model_dump(include=set(ScheduleSettings.model_fields))
                schedule_config = ScheduleSettings.model_validate(data_fields).model_dump(mode="json")
    template = WorkflowTemplate(name=data.name, description=data.description, graph_json=graph.model_dump(mode="json"), schedule=schedule_config)
    db.add(template)
    db.commit()
    return template


@router.post("/{template_id}/copy", response_model=WorkflowRead, status_code=201)
def copy_template(template_id: UUID, data: TemplateCopy, db: Database) -> Workflow:
    template = db.get(WorkflowTemplate, template_id)
    if template is None:
        raise ApiError(404, "TEMPLATE_NOT_FOUND", "Шаблон не найден.")
    get_project(data.project_id, db)
    graph = Graph.model_validate(template.graph_json)
    if issues := validate_draft(graph):
        raise graph_error(issues[0])
    workflow = Workflow(project_id=data.project_id, name=data.name or template.name, graph_json=graph.model_dump(mode="json"))
    db.add(workflow)
    if template.schedule is not None:
        db.flush()
        schedule = Schedule(workflow_id=workflow.id, enabled=False)
        apply_spec(schedule, spec_from_settings(ScheduleSettings.model_validate(template.schedule)))
        db.add(schedule)
    db.commit()
    return workflow
