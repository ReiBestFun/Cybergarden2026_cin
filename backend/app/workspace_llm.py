from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from .config import Settings
from .db.models import WorkspaceLLMProfile

OPENAI_URL = "https://api.openai.com/v1"


def openai_settings(settings: Settings, base_url: str, model: str, key: SecretStr) -> Settings:
    return settings.model_copy(update={
        "llm_provider": "openai_compatible", "llm_base_url": base_url, "llm_model": model, "llm_api_key": key,
    })


def model_settings(db: Session, workspace_id: UUID | None, settings: Settings) -> Settings:
    profile = db.get(WorkspaceLLMProfile, workspace_id) if workspace_id else None
    if profile:
        return openai_settings(settings, profile.base_url, profile.model, SecretStr(profile.api_key))
    if settings.llm_provider == "ollama":
        return settings
    # A legacy server-wide external configuration cannot opt a team into data sharing.
    return settings.model_copy(update={
        "llm_provider": "ollama", "llm_base_url": Settings.model_fields["llm_base_url"].default,
        "llm_model": Settings.model_fields["llm_model"].default, "llm_api_key": None,
    })
