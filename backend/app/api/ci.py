from dataclasses import dataclass
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from ..config import Settings
from ..db.models import Project, ScanRun, Workflow
from ..db.session import Database
from ..integrations.github_actions import RunnerMode, security_workflow
from ..integrations.repository import allowed_repository_urls, is_repository_allowed
from ..nodes.catalog import CiTriggerConfig
from ..schemas.contracts import CiRunCreate, CiRunStatus, CiVerdict, CommitSha, Graph, Input
from ..workflows.queue import enqueue_ci_run
from ..workflows.validation import validate_graph
from .auth import CurrentUser
from .common import ApiError, graph_error, normalize_repository
from .projects import get_project
from .workflows import get_workflow

router = APIRouter(tags=["ci"])
_bearer = HTTPBearer(auto_error=False)


class CiTestRequest(Input):
    commit_sha: CommitSha


@dataclass(frozen=True)
class CiAuthorization:
    settings: Settings
    owner_id: UUID


def _authorize(
    workflow_id: UUID, request: Request, db: Database,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CiAuthorization:
    settings: Settings = request.app.state.settings
    if bearer is not None:
        workflow = db.execute(
            select(Workflow.ci_token_hash, Project.owner_id)
            .join(Project, Workflow.project_id == Project.id)
            .where(Workflow.id == workflow_id, Project.owner_id.is_not(None))
        ).one_or_none()
        if workflow is not None:
            digest, owner_id = workflow
            if digest is not None:
                if compare_digest(digest, sha256(bearer.credentials.encode()).hexdigest()):
                    return CiAuthorization(settings, owner_id)
            else:
                expected = settings.ci_workflow_tokens.get(workflow_id)
                if expected and compare_digest(expected.get_secret_value().encode(), bearer.credentials.encode()):
                    return CiAuthorization(settings, owner_id)
    raise ApiError(401, "CI_UNAUTHORIZED", "Нужен действующий токен CI для этого сценария.")


CiAuth = Annotated[CiAuthorization, Depends(_authorize)]


def _check_repository(project: Project, settings: Settings) -> None:
    if not is_repository_allowed(project.repository_url, allowed_repository_urls(settings.allowed_repositories)):
        raise ApiError(403, "CI_REPOSITORY_NOT_ALLOWED", "Для CI репозиторий должен быть в списке учебных источников.")


def _ci_graph(graph_json: dict) -> Graph:
    graph = Graph.model_validate(graph_json)
    if issues := validate_graph(graph, expected_trigger="ci_trigger"):
        raise graph_error(issues[0])
    return graph


@router.post("/workflows/{workflow_id}/ci-test", status_code=202)
def start_ci_test(workflow_id: UUID, body: CiTestRequest, db: Database, request: Request, user: CurrentUser) -> dict[str, UUID]:
    workflow = get_workflow(workflow_id, db, user.id, lock=True)
    project = get_project(workflow.project_id, db, user.id)
    _check_repository(project, request.app.state.settings)
    graph = _ci_graph(workflow.graph_json)
    run = enqueue_ci_run(db, project, workflow, graph, body.commit_sha, f"web-{uuid4()}")
    db.commit()
    return {"id": run.id}


@router.post("/workflows/{workflow_id}/ci-token")
def issue_token(workflow_id: UUID, body: Input, db: Database, request: Request, response: Response, user: CurrentUser) -> dict[str, str]:
    workflow = get_workflow(workflow_id, db, user.id, lock=True)
    _check_repository(get_project(workflow.project_id, db, user.id), request.app.state.settings)
    _ci_graph(workflow.graph_json)
    token = token_urlsafe(32)
    workflow.ci_token_hash = sha256(token.encode()).hexdigest()
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"token": token}


@router.get("/workflows/{workflow_id}/security.yml", response_class=Response)
def download_workflow(workflow_id: UUID, db: Database, request: Request, user: CurrentUser, mode: RunnerMode = "local") -> Response:
    workflow = get_workflow(workflow_id, db, user.id)
    project = get_project(workflow.project_id, db, user.id)
    _check_repository(project, request.app.state.settings)
    graph = _ci_graph(workflow.graph_json)
    trigger = next(node for node in graph.nodes if node.type == "ci_trigger")
    branch = CiTriggerConfig.model_validate(trigger.config).branch or project.default_ref
    return Response(
        security_workflow(workflow.id, branch, mode), media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="security.yml"'},
    )


@router.post("/hooks/{workflow_id}", status_code=202)
def start_ci_run(workflow_id: UUID, body: CiRunCreate, db: Database, auth: CiAuth) -> dict[str, UUID]:
    workflow = get_workflow(workflow_id, db, auth.owner_id, lock=True)
    project = get_project(workflow.project_id, db, auth.owner_id)
    _check_repository(project, auth.settings)
    if normalize_repository(body.repository_url).casefold() != project.repository_url.casefold():
        raise ApiError(403, "CI_REPOSITORY_MISMATCH", "Репозиторий CI не совпадает с репозиторием проекта.")
    existing = db.scalar(select(ScanRun).where(
        ScanRun.workflow_id == workflow_id, ScanRun.ci_request_id == body.request_id,
    ))
    if existing is not None:
        if existing.source_snapshot["ref"] != body.commit_sha:
            raise ApiError(409, "CI_REQUEST_CONFLICT", "С этим идентификатором CI уже проверяется другой коммит.")
        return {"id": existing.id}
    graph = _ci_graph(workflow.graph_json)
    run = enqueue_ci_run(db, project, workflow, graph, body.commit_sha, body.request_id)
    db.commit()
    return {"id": run.id}


@router.get("/hooks/{workflow_id}/runs/{run_id}", response_model=CiRunStatus)
def get_ci_run(workflow_id: UUID, run_id: UUID, db: Database, auth: CiAuth) -> CiRunStatus:
    run = db.scalar(select(ScanRun).join(Project, Project.id == ScanRun.project_id).where(
        ScanRun.id == run_id, ScanRun.workflow_id == workflow_id, ScanRun.trigger == "ci", Project.owner_id == auth.owner_id,
    ))
    if run is None:
        raise ApiError(404, "CI_RUN_NOT_FOUND", "Запуск CI не найден.")
    result = CiRunStatus(id=run.id, status=run.status, commit_sha=run.commit_sha)
    if run.status in {"queued", "running"}:
        return result
    if run.status in {"succeeded", "partial"}:
        result.report_url = f"{auth.settings.app_base_url.rstrip('/')}/results?run={run.id}"
    gate = (run.report or {}).get("ci_gate")
    if run.error or gate is None:
        reason = run.error["code"] if run.error else "CI_GATE_UNAVAILABLE"
        result.gate = CiVerdict(decision="error", reason=reason)
    elif run.commit_sha != run.source_snapshot["ref"]:
        result.gate = CiVerdict(decision="error", reason="CI_SHA_MISMATCH")
    else:
        result.gate = CiVerdict.model_validate(gate)
    return result
