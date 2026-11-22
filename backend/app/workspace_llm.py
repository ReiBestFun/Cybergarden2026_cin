from typing import Literal
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from .config import Settings
from .db.models import WorkspaceLLMProfile

ExternalProvider = Literal["openai", "gemini", "groq"]
Provider = Literal["ollama", "openai", "gemini", "groq"]

PROVIDER_URLS: dict[ExternalProvider, str] = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
}
EXTERNAL_MODELS = {
    "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
    "gemini": ["gemini-flash-latest", "gemini-3.1-flash-lite"],
    "groq": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
}


def external_settings(settings: Settings, base_url: str, model: str, key: SecretStr) -> Settings:
    return settings.model_copy(update={
        "llm_provider": "openai_compatible", "llm_base_url": base_url, "llm_model": model, "llm_api_key": key,
    })


def ollama_settings(settings: Settings) -> Settings:
    if settings.llm_provider == "ollama":
        return settings
    # A legacy server-wide external configuration cannot opt a team into data sharing.
    return settings.model_copy(update={
        "llm_provider": "ollama", "llm_base_url": Settings.model_fields["llm_base_url"].default,
        "llm_model": Settings.model_fields["llm_model"].default, "llm_api_key": None,
    })


def model_settings(db: Session, workspace_id: UUID | None, settings: Settings) -> Settings:
    profile = db.get(WorkspaceLLMProfile, workspace_id) if workspace_id else None
    if profile is None:
        return ollama_settings(settings)
    if profile.provider == "ollama":
        return ollama_settings(settings).model_copy(update={"llm_model": profile.model})
    return external_settings(settings, profile.base_url, profile.model, SecretStr(profile.api_key))
