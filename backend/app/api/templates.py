"""Private workflow templates and creating a new scenario from a copy."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import Field, StringConstraints
from sqlalchemy import select

from ..db.models import Workflow, WorkflowTemplate
from ..db.session import Database
from ..nodes.catalog import NOTIFIER_CONFIGS
from ..schemas.contracts import Graph, Input, Name, Page, ReadModel, WorkflowRead
from .auth import CurrentUser
from .common import ApiError, PageQuery, page
from .projects import get_project
from .workflows import checked_draft, get_workflow

router = APIRouter(prefix="/templates", tags=["templates"])
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class TemplateCreate(Input):
    workflow_id: UUID
    name: Name
    description: Description = ""


class TemplatePatch(Input):
    workflow_id: UUID | None = None
    name: Name | None = None
    description: Description | None = None


class TemplateRead(ReadModel):
    id: UUID
    name: str
    description: str
    graph: Graph = Field(validation_alias="graph_json")
    created_at: datetime
    updated_at: datetime


class InstantiateTemplate(Input):
    project_id: UUID
    name: Name | None = None


def _template(template_id: UUID, owner_id: UUID, db: Database, *, lock: bool = False) -> WorkflowTemplate:
    query = select(WorkflowTemplate).where(WorkflowTemplate.id == template_id, WorkflowTemplate.owner_id == owner_id)
    template = db.scalar(query.with_for_update() if lock else query)
    if template is None:
        raise ApiError(404, "TEMPLATE_NOT_FOUND", "Шаблон не найден.")
    return template


def _reusable_graph(workflow: Workflow) -> dict:
    graph = Graph.model_validate(workflow.graph_json)
    for node in graph.nodes:
        if node.type in NOTIFIER_CONFIGS:
            node.config.pop("recipients", None)
            node.config.pop("subject", None)
        elif node.type == "ci_trigger":
            node.config.pop("branch", None)
    return checked_draft(graph)


@router.get("", response_model=Page[TemplateRead])
def list_templates(db: Database, user: CurrentUser, pagination: PageQuery) -> dict:
    query = select(WorkflowTemplate).where(WorkflowTemplate.owner_id == user.id).order_by(
        WorkflowTemplate.updated_at.desc(), WorkflowTemplate.id,
    )
    return page(db, query, pagination.limit, pagination.offset)


@router.post("", status_code=201, response_model=TemplateRead)
def create_template(data: TemplateCreate, db: Database, user: CurrentUser) -> WorkflowTemplate:
    workflow = get_workflow(data.workflow_id, db, user.id)
    template = WorkflowTemplate(
        owner_id=user.id, name=data.name, description=data.description, graph_json=_reusable_graph(workflow),
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
        template.graph_json = _reusable_graph(get_workflow(data.workflow_id, db, user.id))
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


@router.post("/{template_id}/instantiate", status_code=201, response_model=WorkflowRead)
def instantiate_template(template_id: UUID, data: InstantiateTemplate, db: Database, user: CurrentUser) -> Workflow:
    template = _template(template_id, user.id, db)
    project = get_project(data.project_id, db, user.id)
    workflow = Workflow(project_id=project.id, name=data.name or template.name, graph_json=template.graph_json)
    db.add(workflow)
    db.commit()
    return workflow
