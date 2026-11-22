import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .api import auth, ci, credentials, delivery_settings, destinations, mcp_access, projects, runs, templates, workflows, workspace_llm, workspaces
from .api import archives, logs
from .api.common import ApiError
from .config import Settings
from .db.session import Database, make_engine
from .integrations.repository import repository_allowlist
from .schemas.contracts import ErrorResponse
from .technical_log import log_event


class ApiRequestLogging:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        request_id = uuid4()
        state = scope.setdefault("state", {})
        status = None

        async def send_response(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = str(request_id)
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except Exception:
            log_event("api", "http.request", "failed", level=logging.ERROR, request_id=request_id,
                      run_id=state.get("run_id"), http_status=status if status is not None else 500)
            if status is not None:
                raise RuntimeError("The CyberFlow API response was interrupted.") from None
            error = ApiError(500, "INTERNAL_ERROR", "Внутренняя ошибка сервера.")
            await JSONResponse(status_code=500, content=ErrorResponse(error=error.error).model_dump())(scope, receive, send_response)
            return
        if status is not None:
            level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
            log_event("api", "http.request", "failed" if status >= 400 else "succeeded", level=level,
                      request_id=request_id, run_id=state.get("run_id"), http_status=status)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url, settings.data_encryption_key)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Raw access logs include query parameters such as OAuth callback credentials.
        logging.getLogger("uvicorn.access").disabled = True
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="AI Security Workflow", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.allowed_repositories = repository_allowlist(settings.allowed_repositories, settings.allow_any_repository)
    app.add_middleware(ApiRequestLogging)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.frontend_origins,
        allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type"], expose_headers=["X-Request-ID"],
    )

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content=ErrorResponse(error=error.error).model_dump())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ()))
        return await handle_api_error(request, ApiError(
            422, "INVALID_REQUEST", "Проверьте формат параметров запроса.", field=field or None,
        ))

    @app.exception_handler(OperationalError)
    async def handle_database_error(request: Request, _: OperationalError) -> JSONResponse:
        return await handle_api_error(request, ApiError(503, "DATABASE_UNAVAILABLE", "База данных недоступна."))

    api = APIRouter(prefix="/api", responses={422: {"model": ErrorResponse}})

    @api.get("/health", tags=["health"], responses={503: {"model": ErrorResponse}})
    def health(db: Database) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}

    api.include_router(auth.router)
    api.include_router(projects.router)
    api.include_router(archives.router)
    api.include_router(workspaces.router)
    api.include_router(logs.router)
    api.include_router(delivery_settings.router)
    api.include_router(workspace_llm.router)
    api.include_router(mcp_access.router)
    api.include_router(workflows.router)
    api.include_router(runs.router)
    api.include_router(ci.router)
    api.include_router(credentials.router)
    api.include_router(templates.router)
    api.include_router(destinations.router)
    app.include_router(api)
    return app


try:
    app = create_app()
except Exception:
    log_event("api", "process.startup", "failed", level=logging.ERROR)
    raise RuntimeError("CyberFlow API startup failed.") from None
