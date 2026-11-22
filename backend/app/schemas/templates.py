from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from .contracts import Graph, Input, Name, ReadModel, ScheduleSettings


class TemplatePublish(Input):
    workspace_id: UUID | None = None
    workflow_id: UUID
    name: Name
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class DraftPublication(Input):
    workspace_id: UUID


class TemplateRead(ReadModel):
    id: UUID
    workspace_id: UUID | None
    draft_source_workspace_id: UUID | None
    name: str
    description: str
    graph: Graph = Field(validation_alias="graph_json")
    schedule: ScheduleSettings | None
    created_at: datetime | None
    updated_at: datetime | None
    can_edit: bool
    is_builtin: bool = False


class TemplateCopy(Input):
    project_id: UUID
    name: Name | None = None
