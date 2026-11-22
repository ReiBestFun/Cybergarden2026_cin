import re
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select

from ..analyzers.models import Category, Severity
from ..db.models import Project, ScanRun, Workflow, WorkspaceMcpToken, WorkspaceMembership
from ..db.session import Database
from ..workspace_logs import audit_action
from ..schemas.contracts import Graph, Input, Name, Page, ReadModel, RunStatus, RunSummary
from .auth import CurrentUser, token_hash
from .common import ApiError, page
from .runs import _findings, _run_summary
from .workflows import checked_draft
from .workspaces import require_workspace_owner, workspace_role

router = APIRouter(tags=["mcp"])
_bearer = HTTPBearer(auto_error=False)
_token_pattern = re.compile(r"^mcp_[A-Za-z0-9_-]{43}$")
Limit = Annotated[int, Query(ge=1, le=50)]
Offset = Annotated[int, Query(ge=0)]


class TokenCreate(Input):
    name: Name


class TokenRead(ReadModel):
    id: UUID
    name: str
    expires_at: datetime


class TokenIssued(TokenRead):
    token: str


class RunItem(ReadModel):
    id: UUID
    status: RunStatus
    created_at: datetime
    project_name: str
    workflow_name: str


class McpSummary(ReadModel):
    id: UUID
    status: RunStatus
    summary: RunSummary
    coverage: Literal["complete", "partial", "unavailable"] | None
    ai_status: str | None


class McpFinding(ReadModel):
    id: UUID
    category: Category
    rule_id: str
    severity: Severity
    title: str
    path: str
    line: int | None
    end_line: int | None
    advisory_id: str | None


@router.get("/workspaces/{workspace_id}/mcp-tokens", response_model=list[TokenRead])
def list_tokens(workspace_id: UUID, user: CurrentUser, db: Database):
    workspace_role(db, workspace_id, user.id)
    return db.scalars(select(WorkspaceMcpToken).where(
        WorkspaceMcpToken.workspace_id == workspace_id, WorkspaceMcpToken.user_id == user.id,
        WorkspaceMcpToken.expires_at > func.now(),
    ).order_by(WorkspaceMcpToken.expires_at.desc(), WorkspaceMcpToken.id)).all()


@router.post("/workspaces/{workspace_id}/mcp-tokens", status_code=201)
def issue_token(workspace_id: UUID, data: TokenCreate, user: CurrentUser, db: Database) -> TokenIssued:
    audit_action(db, workspace_id, user.id, "mcp.issue")
    membership = db.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.user_id == user.id,
    ).with_for_update())
    if membership is None:
        raise ApiError(404, "WORKSPACE_NOT_FOUND", "Рабочее пространство не найдено.")
    db.execute(delete(WorkspaceMcpToken).where(
        WorkspaceMcpToken.workspace_id == workspace_id, WorkspaceMcpToken.user_id == user.id,
        WorkspaceMcpToken.expires_at <= func.now(),
    ))
    token = "mcp_" + token_urlsafe(32)
    row = WorkspaceMcpToken(
        workspace_id=workspace_id, user_id=user.id, name=data.name, token_hash=token_hash(token),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(row)
    db.commit()
    return TokenIssued(id=row.id, name=row.name, expires_at=row.expires_at, token=token)


@router.delete("/workspaces/{workspace_id}/mcp-tokens/{token_id}", status_code=204)
def revoke_token(workspace_id: UUID, token_id: UUID, user: CurrentUser, db: Database) -> Response:
    audit = audit_action(db, workspace_id, user.id, "mcp.revoke")
    workspace_role(db, workspace_id, user.id)
    deleted = db.scalar(delete(WorkspaceMcpToken).where(
        WorkspaceMcpToken.id == token_id, WorkspaceMcpToken.workspace_id == workspace_id, WorkspaceMcpToken.user_id == user.id,
    ).returning(WorkspaceMcpToken.id))
    if deleted is None:
        audit.result = "unchanged"
    db.commit()
    return Response(status_code=204)


def _mcp_workspace(
    db: Database, bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> UUID:
    if bearer and _token_pattern.fullmatch(bearer.credentials):
        workspace_id = db.scalar(select(WorkspaceMcpToken.workspace_id).where(
            WorkspaceMcpToken.token_hash == token_hash(bearer.credentials), WorkspaceMcpToken.expires_at > func.now(),
        ))
        if workspace_id is not None:
            return workspace_id
    raise ApiError(401, "MCP_UNAUTHORIZED", "Укажите действующий токен MCP.")


McpWorkspace = Annotated[UUID, Depends(_mcp_workspace)]


@router.get("/mcp/runs", response_model=Page[RunItem])
def list_runs(
    workspace_id: McpWorkspace, db: Database, limit: Limit = 20, offset: Offset = 0, status: RunStatus | None = None,
) -> dict:
    statement = select(
        ScanRun.id, ScanRun.status, ScanRun.created_at, Project.name.label("project_name"), Workflow.name.label("workflow_name"),
    ).join(Project, Project.id == ScanRun.project_id).join(Workflow, Workflow.id == ScanRun.workflow_id).where(
        Project.workspace_id == workspace_id,
    )
    if status is not None:
        statement = statement.where(ScanRun.status == status)
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    items = db.execute(statement.order_by(ScanRun.created_at.desc(), ScanRun.id.desc()).limit(limit).offset(offset)).mappings().all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _get_run(run_id: UUID, workspace_id: UUID, request: Request, db: Database) -> ScanRun:
    run = db.scalar(select(ScanRun).join(Project, Project.id == ScanRun.project_id).where(
        ScanRun.id == run_id, Project.workspace_id == workspace_id,
    ))
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "Запуск не найден.")
    request.state.run_id = run.id
    return run


@router.get("/mcp/runs/{run_id}/summary")
def read_summary(run_id: UUID, workspace_id: McpWorkspace, db: Database, request: Request) -> McpSummary:
    run = _get_run(run_id, workspace_id, request, db)
    report = run.report or {}
    return McpSummary(
        id=run.id, status=run.status, summary=_run_summary(db, run),
        coverage=(report.get("coverage") or {}).get("status"), ai_status=(report.get("llm") or {}).get("status"),
    )


@router.get("/mcp/runs/{run_id}/findings", response_model=Page[McpFinding])
def read_findings(
    run_id: UUID, workspace_id: McpWorkspace, db: Database, request: Request, limit: Limit = 20, offset: Offset = 0,
) -> dict:
    _get_run(run_id, workspace_id, request, db)
    result = page(db, _findings(run_id), limit, offset)
    result["items"] = [
        McpFinding(id=row.id, category=row.category, rule_id=row.rule_id, severity=row.severity,
                   title=row.title, path=row.path, line=row.line, end_line=row.end_line,
                   advisory_id=row.finding_metadata.get("advisory_id"))
        for row in result["items"]
    ]
    return result


@router.post("/workspaces/{workspace_id}/workflow-drafts/validate", response_model=Graph)
def validate_editor_draft(workspace_id: UUID, graph: Graph, user: CurrentUser, db: Database) -> dict:
    require_workspace_owner(db, workspace_id, user.id)
    return checked_draft(graph)
