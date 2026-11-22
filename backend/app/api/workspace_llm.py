from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr, StringConstraints

from ..config import Settings
from ..db.models import WorkspaceLLMProfile
from ..db.session import Database
from ..llm import ModelError, complete_json
from ..schemas.contracts import Input, ReadModel
from ..workspace_llm import OPENAI_URL, model_settings, openai_settings
from .auth import CurrentUser
from .common import ApiError
from .workspaces import require_team_owner

router = APIRouter(prefix="/workspaces/{workspace_id}/llm", tags=["workspace LLM"])


class LocalProfile(Input):
    provider: Literal["ollama"]


class OpenAIProfile(Input):
    provider: Literal["openai"]
    model: Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")]
    api_key: SecretStr = Field(min_length=1, max_length=4096)


class ProfileRead(ReadModel):
    provider: Literal["ollama", "openai"]
    model: str


class ProbeResponse(Input):
    ok: Literal[True]


@router.get("", response_model=ProfileRead)
def read_profile(workspace_id: UUID, user: CurrentUser, db: Database, request: Request) -> ProfileRead:
    require_team_owner(db, workspace_id, user.id, lock=False)
    settings = model_settings(db, workspace_id, request.app.state.settings)
    return ProfileRead(provider="ollama" if settings.llm_provider == "ollama" else "openai", model=settings.llm_model)


@router.put("", response_model=ProfileRead)
def configure_profile(
    workspace_id: UUID, data: LocalProfile | OpenAIProfile, user: CurrentUser, db: Database, request: Request,
) -> ProfileRead:
    settings: Settings = request.app.state.settings
    require_team_owner(db, workspace_id, user.id, lock=False)
    if isinstance(data, OpenAIProfile):
        secret = data.api_key.get_secret_value()
        if not secret.isascii() or not secret.isprintable() or any(char.isspace() for char in secret):
            raise ApiError(422, "INVALID_MODEL_KEY", "Проверьте формат API-ключа.")
        candidate = openai_settings(settings, OPENAI_URL, data.model, data.api_key)
        try:
            complete_json(candidate, ProbeResponse, system="Return only a JSON object.",
                          user='Return {"ok":true}.')
        except ModelError as error:
            raise ApiError(502, "MODEL_ERROR", str(error)) from None
    require_team_owner(db, workspace_id, user.id)
    if isinstance(data, OpenAIProfile):
        db.merge(WorkspaceLLMProfile(workspace_id=workspace_id, base_url=OPENAI_URL,
                                     model=data.model, api_key=data.api_key.get_secret_value()))
        result = ProfileRead(provider="openai", model=data.model)
    else:
        row = db.get(WorkspaceLLMProfile, workspace_id)
        if row is not None:
            db.delete(row)
        result = ProfileRead(provider="ollama", model=model_settings(db, None, settings).llm_model)
    db.commit()
    return result
