from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str = "postgresql+psycopg://workflow:workflow@127.0.0.1:5432/workflow"
    data_encryption_key: SecretStr | None = None
    log_destinations: dict[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,47}$")], Path] = Field(default_factory=dict)

    @field_validator("log_destinations")
    @classmethod
    def validate_log_destinations(cls, values: dict[str, Path]) -> dict[str, Path]:
        if "postgresql" in values or any(not path.is_absolute() for path in values.values()):
            raise ValueError("Use a dedicated alias and an absolute mount path for each log destination.")
        return values
    github_client_id: str = ""
    github_client_secret: SecretStr | None = None
    frontend_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    allowed_repositories: list[str] = ["https://github.com/TBagirov/training"]
    # Development switch: accept any public GitHub repository instead of the allowlist above.
    allow_any_repository: bool = False
    sca_catalog_path: Path | None = None
    repository_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    scan_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    scan_parallelism: int = Field(default=3, ge=1, le=3)
    worker_poll_seconds: float = Field(default=1, gt=0, allow_inf_nan=False)
    scheduler_poll_seconds: float = Field(default=15, gt=0, allow_inf_nan=False)
    worker_stale_seconds: float = Field(default=180, ge=15, allow_inf_nan=False)
    ci_workflow_tokens: dict[UUID, SecretStr] = Field(default_factory=dict)
    credentials_encryption_key: SecretStr | None = None
    llm_provider: Literal["ollama", "openai_compatible"] = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.5:4b"
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=90, gt=0, le=120)

    # Notifications. Secrets stay on the server and never enter workflow graphs.
    app_base_url: str = "http://localhost:5173"
    notify_timeout_seconds: float = Field(default=15, gt=0, allow_inf_nan=False)
    notify_poll_seconds: float = Field(default=1, gt=0, allow_inf_nan=False)
    notify_max_attempts: int = Field(default=4, ge=1, le=12)
    notify_max_attachment_bytes: int = Field(default=2 * 1024 * 1024, ge=0)
    notify_email_recipients: list[str] = Field(default_factory=list)
    notify_telegram_recipients: list[str] = Field(default_factory=list)
    telegram_bot_token: SecretStr | None = None
    telegram_api_base: str = "https://api.telegram.org"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, gt=0, lt=65536)
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
