from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, StringConstraints

from ..analyzers.models import Category, Severity
from ..integrations.repository import REF_PATTERN

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Ref = Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=REF_PATTERN)]
GraphId = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
NodeType = Annotated[str, StringConstraints(min_length=1, max_length=64)]
RunStatus = Literal["queued", "running", "succeeded", "partial", "failed"]
NodeStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
Trigger = Literal["manual", "schedule"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Error(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    field: str | None = None


class ErrorResponse(BaseModel):
    error: Error


class Issue(Error):
    edge_id: str | None = None


class Position(Input):
    x: FiniteFloat
    y: FiniteFloat


class Node(Input):
    id: GraphId
    type: NodeType
    config: dict[str, JsonValue] = Field(default_factory=dict)
    position: Position


class Edge(Input):
    id: GraphId
    source: GraphId
    source_port: GraphId
    target: GraphId
    target_port: GraphId


class Graph(Input):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class ValidateWorkflow(Input):
    graph: Graph | None = None


class ValidationResult(BaseModel):
    valid: bool
    issues: list[Issue]


class ProjectCreate(Input):
    name: Name
    repository_url: Annotated[str, StringConstraints(max_length=2048)]
    default_ref: Ref = "main"


class ProjectRead(ReadModel):
    id: UUID
    name: str
    repository_url: str
    default_ref: str
    created_at: datetime


class WorkflowCreate(Input):
    name: Name
    graph: Graph = Field(default_factory=Graph)


class WorkflowPatch(Input):
    name: Name | None = None
    graph: Graph | None = None


class WorkflowRead(ReadModel):
    id: UUID
    project_id: UUID
    name: str
    graph: Graph = Field(validation_alias="graph_json")
    created_at: datetime
    updated_at: datetime


Frequency = Literal["daily", "weekly", "monthly", "cron"]
Weekday = Annotated[int, Field(ge=0, le=6)]  # 0 = Monday … 6 = Sunday


class ScheduleSettings(Input):
    frequency: Frequency = "daily"
    time: Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")] | None = None
    weekdays: Annotated[list[Weekday], Field(max_length=7)] | None = None
    day_of_month: Annotated[int, Field(ge=1, le=31)] | None = None
    cron: Annotated[str, StringConstraints(max_length=120)] | None = None
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None


class ScheduleUpdate(ScheduleSettings):
    enabled: bool


class SchedulePreview(BaseModel):
    next_runs: list[datetime]


class ScheduleRead(BaseModel):
    enabled: bool = False
    frequency: Frequency = "daily"
    time: str | None = None
    weekdays: list[int] | None = None
    day_of_month: int | None = None
    cron: str | None = None
    timezone: str | None = None
    next_run_at: datetime | None = None
    upcoming: list[datetime] = Field(default_factory=list)
    last_enqueued_at: datetime | None = None
    last_error: Error | None = None


class RunCreate(Input):
    ref: Ref | None = None


class RunQueued(ReadModel):
    id: UUID
    status: Literal["queued"]


class RunSummary(BaseModel):
    total: int = 0
    high_count: int = 0
    critical_count: int = 0


class RunRead(ReadModel):
    id: UUID
    project_id: UUID
    workflow_id: UUID
    trigger: Trigger
    status: RunStatus
    source_snapshot: dict[str, str]
    commit_sha: str | None
    scheduled_for: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: RunSummary | None
    error: Error | None


class NodeRunRead(ReadModel):
    node_id: str
    type: str
    status: NodeStatus
    started_at: datetime | None
    finished_at: datetime | None
    active_output_ports: list[str]
    input_preview: dict[str, JsonValue] | None
    output_preview: dict[str, JsonValue] | None
    error: Error | None


class RunDetail(RunRead):
    graph_snapshot: Graph
    nodes: list[NodeRunRead]


class FindingRead(ReadModel):
    id: UUID
    node_run_id: UUID
    rule_id: str
    category: Category
    severity: Severity
    title: str
    description: str
    remediation: str
    path: str
    line: int | None
    end_line: int | None
    evidence: str
    metadata: dict[str, str] = Field(validation_alias="details")


class RunReportRead(BaseModel):
    status: RunStatus
    summary: RunSummary
    coverage: dict[str, JsonValue] = Field(default_factory=dict)
    llm: dict[str, JsonValue] | None = None
    delivery: dict[str, JsonValue] | None = None
    error: Error | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
