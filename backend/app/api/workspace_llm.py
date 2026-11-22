from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr, StringConstraints
from sqlalchemy import select

from ..config import Settings
from ..db.models import WorkspaceLLMProfile
from ..db.session import Database
from ..llm import ModelError, complete_json, installed_ollama_models
from ..schemas.contracts import Input, ReadModel
from ..workspace_llm import EXTERNAL_MODELS, PROVIDER_URLS, ExternalProvider, Provider, external_settings, ollama_settings
from .auth import CurrentUser
from .common import ApiError
from .workspaces import require_team_owner

router = APIRouter(prefix="/workspaces/{workspace_id}/llm", tags=["workspace LLM"])
ModelId = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_./:-]+$")]


class LocalProfile(Input):
    provider: Literal["ollama"]
    model: ModelId | None = None


class ExternalProfile(Input):
    provider: ExternalProvider
    model: ModelId
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class ProfileRead(ReadModel):
    provider: Provider
    model: str


class ProbeResponse(Input):
    ok: Literal[True]


@router.get("", response_model=ProfileRead)
def read_profile(workspace_id: UUID, user: CurrentUser, db: Database, request: Request) -> ProfileRead:
    require_team_owner(db, workspace_id, user.id, lock=False)
    row = db.get(WorkspaceLLMProfile, workspace_id)
    if row:
        return ProfileRead(provider=row.provider, model=row.model)
    return ProfileRead(provider="ollama", model=ollama_settings(request.app.state.settings).llm_model)


@router.get("/models", response_model=dict[str, list[str]])
def list_models(workspace_id: UUID, user: CurrentUser, db: Database, request: Request) -> dict[str, list[str]]:
    require_team_owner(db, workspace_id, user.id, lock=False)
    local = ollama_settings(request.app.state.settings)
    models = {**EXTERNAL_MODELS, "ollama": list(dict.fromkeys([local.llm_model, *installed_ollama_models(local)]))}
    row = db.get(WorkspaceLLMProfile, workspace_id)
    if row and row.model not in models[row.provider]:
        models[row.provider] = [row.model, *models[row.provider]]
    return models


@router.put("", response_model=ProfileRead)
def configure_profile(
    workspace_id: UUID, data: LocalProfile | ExternalProfile, user: CurrentUser, db: Database, request: Request,
) -> ProfileRead:
    settings: Settings = request.app.state.settings
    require_team_owner(db, workspace_id, user.id, lock=False)
    local = ollama_settings(settings)
    if isinstance(data, LocalProfile) and data.model in {None, local.llm_model}:
        require_team_owner(db, workspace_id, user.id)
        existing = db.get(WorkspaceLLMProfile, workspace_id)
        if existing is not None:
            db.delete(existing)
        db.commit()
        return ProfileRead(provider="ollama", model=local.llm_model)

    key = None
    reused_key = False
    if isinstance(data, ExternalProfile):
        key = data.api_key
        if key is None:
            previous = db.get(WorkspaceLLMProfile, workspace_id)
            if previous is None or previous.provider != data.provider or not previous.api_key:
                raise ApiError(422, "MODEL_KEY_REQUIRED", "Введите API-ключ выбранного провайдера.")
            key = SecretStr(previous.api_key)
            reused_key = True
        secret = key.get_secret_value()
        if not secret.isascii() or not secret.isprintable() or any(char.isspace() for char in secret):
            raise ApiError(422, "INVALID_MODEL_KEY", "Проверьте формат API-ключа.")
        candidate = external_settings(settings, PROVIDER_URLS[data.provider], data.model, key)
    else:
        if data.model not in installed_ollama_models(local):
            raise ApiError(422, "UNKNOWN_LOCAL_MODEL", "Модель отсутствует в списке Ollama на сервере.")
        candidate = local.model_copy(update={"llm_model": data.model})
    try:
        complete_json(candidate, ProbeResponse, system="Return only a JSON object.", user='Return {"ok":true}.')
    except ModelError as error:
        raise ApiError(502, "MODEL_ERROR", str(error)) from None

    require_team_owner(db, workspace_id, user.id)
    if reused_key:
        row = db.scalar(select(WorkspaceLLMProfile).where(WorkspaceLLMProfile.workspace_id == workspace_id)
                        .execution_options(populate_existing=True))
        if row is None or row.provider != data.provider or row.api_key != key.get_secret_value():
            raise ApiError(409, "MODEL_KEY_CHANGED", "Ключ изменился. Обновите страницу.")
    db.merge(WorkspaceLLMProfile(workspace_id=workspace_id, provider=data.provider, base_url=candidate.llm_base_url,
                                 model=candidate.llm_model, api_key=key.get_secret_value() if key else None))
    db.commit()
    return ProfileRead(provider=data.provider, model=candidate.llm_model)
