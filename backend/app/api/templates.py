"""Private workflow templates and creating a new scenario from a copy."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import StringConstraints
from sqlalchemy import and_, or_, select

from ..db.models import Schedule, Workflow, WorkflowTemplate, WorkspaceMembership
from ..db.session import Database
from ..nodes.catalog import NOTIFIER_CONFIGS
from ..schemas.contracts import Graph, Input, Name, Page, ScheduleSettings, WorkflowRead
from ..schemas.templates import TemplateCopy, TemplatePublish, TemplateRead
from ..workflows.schedules import apply_spec, schedule_read, spec_from_settings
from .auth import CurrentUser
from .common import ApiError, PageQuery, page
from .projects import get_project
from .workflows import checked_draft, get_workflow
from .workspaces import require_workspace_owner, workspace_role

router = APIRouter(prefix="/templates", tags=["templates"])
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class TemplatePatch(Input):
    workflow_id: UUID | None = None
    name: Name | None = None
    description: Description | None = None


def _visible_template(owner_id: UUID):
    teams = select(WorkspaceMembership.workspace_id).where(WorkspaceMembership.user_id == owner_id)
    return or_(WorkflowTemplate.owner_id == owner_id, WorkflowTemplate.workspace_id.in_(teams),
               and_(WorkflowTemplate.owner_id.is_(None), WorkflowTemplate.workspace_id.is_(None)))


def _template(template_id: UUID, owner_id: UUID, db: Database, *, lock: bool = False) -> WorkflowTemplate:
    visibility = WorkflowTemplate.owner_id == owner_id if lock else _visible_template(owner_id)
    query = select(WorkflowTemplate).where(WorkflowTemplate.id == template_id, visibility)
    template = db.scalar(query.with_for_update() if lock else query)
    if template is None:
        raise ApiError(404, "TEMPLATE_NOT_FOUND", "Шаблон не найден.")
    return template


def _reusable_graph(workflow: Workflow) -> dict:
    graph = Graph.model_validate(workflow.graph_json)
    for node in graph.nodes:
        node.config.pop("credential_id", None)
        if node.type in NOTIFIER_CONFIGS:
            node.config.pop("recipients", None)
            node.config.pop("subject", None)
        elif node.type == "ci_trigger":
            node.config.pop("branch", None)
    return checked_draft(graph)


def _saved_schedule(db: Database, workflow: Workflow) -> dict | None:
    row = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow.id))
    if row is None or row.timezone is None:
        return None
    data = schedule_read(row).model_dump(include=set(ScheduleSettings.model_fields))
    return ScheduleSettings.model_validate(data).model_dump(mode="json")


@router.get("", response_model=Page[TemplateRead])
def list_templates(db: Database, user: CurrentUser, pagination: PageQuery, workspace_id: UUID | None = None) -> dict:
    query = select(WorkflowTemplate).where(_visible_template(user.id)).order_by(
        WorkflowTemplate.updated_at.desc(), WorkflowTemplate.id,
    )
    if workspace_id is not None:
        workspace_role(db, workspace_id, user.id)
        query = query.where(or_(WorkflowTemplate.workspace_id == workspace_id, WorkflowTemplate.workspace_id.is_(None)))
    return page(db, query, pagination.limit, pagination.offset)


@router.post("", status_code=201, response_model=TemplateRead)
def create_template(data: TemplatePublish, db: Database, user: CurrentUser) -> WorkflowTemplate:
    workflow = get_workflow(data.workflow_id, db, user.id)
    if data.workspace_id is not None:
        require_workspace_owner(db, data.workspace_id, user.id)
        project = get_project(workflow.project_id, db, user.id)
        if data.workspace_id != project.workspace_id or data.workspace_id == user.id:
            raise ApiError(409, "TEAM_WORKSPACE_REQUIRED", "Выберите командное пространство исходного сценария.")
    template = WorkflowTemplate(
        workspace_id=data.workspace_id, owner_id=user.id if data.workspace_id is None else None,
        name=data.name, description=data.description, graph_json=_reusable_graph(workflow),
        schedule=_saved_schedule(db, workflow),
    )
    db.add(template)
    db.commit()
    return template


@router.get("/{template_id}", response_model=TemplateRead)
def template_detail(template_id: UUID, db: Database, user: CurrentUser) -> WorkflowTemplate:
    return _template(template_id, user.id, db)


@router.patch("/{template_id}", response_model=TemplateRead)
def update_template(template_id: UUID, data: TemplatePatch, db: Database, user: CurrentUser) -> WorkflowTemplate:
    template = _template(template_id, user.id, db, lock=True)
    if data.workflow_id is not None:
        workflow = get_workflow(data.workflow_id, db, user.id)
        template.graph_json = _reusable_graph(workflow)
        template.schedule = _saved_schedule(db, workflow)
    if data.name is not None:
        template.name = data.name
    if data.description is not None:
        template.description = data.description
    db.commit()
    return template


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: UUID, db: Database, user: CurrentUser) -> Response:
    db.delete(_template(template_id, user.id, db, lock=True))
    db.commit()
    return Response(status_code=204)


@router.post("/{template_id}/copy", status_code=201, response_model=WorkflowRead)
@router.post("/{template_id}/instantiate", status_code=201, response_model=WorkflowRead)
def instantiate_template(template_id: UUID, data: TemplateCopy, db: Database, user: CurrentUser) -> Workflow:
    template = _template(template_id, user.id, db)
    project = get_project(data.project_id, db, user.id, owner_required=True)
    workflow = Workflow(project_id=project.id, name=data.name or template.name, graph_json=template.graph_json)
    db.add(workflow)
    if template.schedule is not None:
        db.flush()
        schedule = Schedule(workflow_id=workflow.id, enabled=False)
        apply_spec(schedule, spec_from_settings(ScheduleSettings.model_validate(template.schedule)))
        db.add(schedule)
    db.commit()
    return workflow
