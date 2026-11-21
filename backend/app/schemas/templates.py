from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from .contracts import Graph, Input, Name, ReadModel, ScheduleSettings


class TemplatePublish(Input):
    workflow_id: UUID
    name: Name
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class TemplateRead(ReadModel):
    id: UUID
    name: str
    description: str
    graph: Graph = Field(validation_alias="graph_json")
    schedule: ScheduleSettings | None
    created_at: datetime
    updated_at: datetime
    can_edit: bool


class TemplateCopy(Input):
    project_id: UUID
    name: Name | None = None
