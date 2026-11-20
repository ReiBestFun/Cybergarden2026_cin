from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://workflow:workflow@127.0.0.1:5432/workflow"
    frontend_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    allowed_repositories: list[str] = ["https://github.com/TBagirov/training"]
    sca_catalog_path: Path | None = None
    repository_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    scan_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    worker_poll_seconds: float = Field(default=1, gt=0, allow_inf_nan=False)
    worker_stale_seconds: float = Field(default=180, ge=15, allow_inf_nan=False)
