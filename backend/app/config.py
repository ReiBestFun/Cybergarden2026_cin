from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://workflow:workflow@127.0.0.1:5432/workflow"
    frontend_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    allowed_repositories: list[str] = ["https://github.com/TBagirov/training"]
    # Development switch: accept any public GitHub repository instead of the allowlist above.
    allow_any_repository: bool = False
    sca_catalog_path: Path | None = None
    repository_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    scan_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    scan_parallelism: int = Field(default=2, ge=1, le=3)
    worker_poll_seconds: float = Field(default=1, gt=0, allow_inf_nan=False)
    scheduler_poll_seconds: float = Field(default=15, gt=0, allow_inf_nan=False)
    worker_stale_seconds: float = Field(default=180, ge=15, allow_inf_nan=False)

    # Notifications. Secrets stay on the server and never enter workflow graphs.
    app_base_url: str = "http://localhost:5173"
    notify_timeout_seconds: float = Field(default=15, gt=0, allow_inf_nan=False)
    telegram_bot_token: SecretStr | None = None
    telegram_api_base: str = "https://api.telegram.org"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, gt=0, lt=65536)
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
