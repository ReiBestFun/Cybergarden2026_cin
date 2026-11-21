from secrets import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from ..config import Settings
from ..db.models import Project, ScanRun
from ..db.session import Database
from ..integrations.github_actions import RunnerMode, security_workflow
from ..integrations.repository import allowed_repository_urls, is_repository_allowed
from ..nodes.catalog import CiTriggerConfig
from ..schemas.contracts import CiRunCreate, CiRunStatus, CiVerdict, Graph
from ..workflows.queue import enqueue_ci_run
from ..workflows.validation import validate_graph
from .common import ApiError, graph_error, normalize_repository
from .projects import get_project
from .workflows import get_workflow

router = APIRouter(tags=["ci"])
_bearer = HTTPBearer(auto_error=False)


def _authorize(
    workflow_id: UUID, request: Request,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Settings:
    settings: Settings = request.app.state.settings
    expected = settings.ci_workflow_tokens.get(workflow_id)
    if expected is None or not expected.get_secret_value() or bearer is None or not compare_digest(
        expected.get_secret_value().encode(), bearer.credentials.encode(),
    ):
        raise ApiError(401, "CI_UNAUTHORIZED", "Нужен действующий токен CI для этого сценария.")
    return settings


CiSettings = Annotated[Settings, Depends(_authorize)]


def _check_repository(project: Project, settings: Settings) -> None:
    if not is_repository_allowed(project.repository_url, allowed_repository_urls(settings.allowed_repositories)):
        raise ApiError(403, "CI_REPOSITORY_NOT_ALLOWED", "Для CI репозиторий должен быть в списке учебных источников.")


def _ci_graph(graph_json: dict) -> Graph:
    graph = Graph.model_validate(graph_json)
    if issues := validate_graph(graph, expected_trigger="ci_trigger"):
        raise graph_error(issues[0])
    return graph


@router.get("/workflows/{workflow_id}/security.yml", response_class=Response)
def download_workflow(workflow_id: UUID, db: Database, request: Request, mode: RunnerMode = "local") -> Response:
    workflow = get_workflow(workflow_id, db)
    project = get_project(workflow.project_id, db)
    _check_repository(project, request.app.state.settings)
    graph = _ci_graph(workflow.graph_json)
    trigger = next(node for node in graph.nodes if node.type == "ci_trigger")
    branch = CiTriggerConfig.model_validate(trigger.config).branch or project.default_ref
    return Response(
        security_workflow(workflow.id, branch, mode), media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="security.yml"'},
    )


@router.post("/hooks/{workflow_id}", status_code=202)
def start_ci_run(workflow_id: UUID, body: CiRunCreate, db: Database, settings: CiSettings) -> dict[str, UUID]:
    workflow = get_workflow(workflow_id, db, lock=True)
    project = get_project(workflow.project_id, db)
    _check_repository(project, settings)
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
def get_ci_run(workflow_id: UUID, run_id: UUID, db: Database, settings: CiSettings) -> CiRunStatus:
    run = db.get(ScanRun, run_id)
    if run is None or run.workflow_id != workflow_id or run.trigger != "ci":
        raise ApiError(404, "CI_RUN_NOT_FOUND", "Запуск CI не найден.")
    result = CiRunStatus(id=run.id, status=run.status, commit_sha=run.commit_sha)
    if run.status in {"queued", "running"}:
        return result
    if run.status in {"succeeded", "partial"}:
        result.report_url = f"{settings.app_base_url.rstrip('/')}/results?run={run.id}"
    gate = (run.report or {}).get("ci_gate")
    if run.error or gate is None:
        reason = run.error["code"] if run.error else "CI_GATE_UNAVAILABLE"
        result.gate = CiVerdict(decision="error", reason=reason)
    elif run.commit_sha != run.source_snapshot["ref"]:
        result.gate = CiVerdict(decision="error", reason="CI_SHA_MISMATCH")
    else:
        result.gate = CiVerdict.model_validate(gate)
    return result
