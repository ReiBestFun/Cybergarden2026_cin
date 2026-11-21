from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from .api import ci, credentials, projects, runs, templates, workflows
from .api.common import ApiError
from .config import Settings
from .db.session import Database, make_engine
from .integrations.repository import repository_allowlist
from .schemas.contracts import ErrorResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="AI Security Workflow", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.allowed_repositories = repository_allowlist(settings.allowed_repositories, settings.allow_any_repository)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.frontend_origins,
        allow_methods=["GET", "POST", "PATCH", "PUT"], allow_headers=["Content-Type"],
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

    api.include_router(projects.router)
    api.include_router(workflows.router)
    api.include_router(runs.router)
    api.include_router(ci.router)
    api.include_router(credentials.router)
    api.include_router(templates.router)
    app.include_router(api)
    return app


app = create_app()
