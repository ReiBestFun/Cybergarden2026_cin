"""Shared provider client for structured model responses."""

import json
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from .config import Settings


class ModelError(Exception):
    pass


def _provider_headers(settings: Settings) -> dict[str, str]:
    try:
        url = urlsplit(settings.llm_base_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError
    except ValueError:
        raise ModelError("Проверьте базовый URL модели в настройках воркера.") from None
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ModelError("Для удалённой модели требуется HTTPS.")
    headers = {}
    if settings.llm_api_key and settings.llm_api_key.get_secret_value():
        headers["Authorization"] = "Bearer " + settings.llm_api_key.get_secret_value()
    return headers


def installed_ollama_models(settings: Settings) -> list[str]:
    try:
        headers = _provider_headers(settings)
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(settings.llm_base_url.rstrip("/") + "/api/tags", headers=headers)
            response.raise_for_status()
            return sorted({model["name"] for model in response.json()["models"] if isinstance(model["name"], str)})
    except (ModelError, httpx.HTTPError, httpx.InvalidURL, TypeError, KeyError, ValueError):
        return []


def complete_json[T: BaseModel](
    settings: Settings, schema: type[T], *, system: str, user: str, constrain_ollama_schema: bool = True,
) -> T:
    headers = _provider_headers(settings)
    native = settings.llm_provider == "ollama"
    endpoint = "/api/chat" if native else "/chat/completions"
    response_schema = schema.model_json_schema()
    schema_json = json.dumps(response_schema, ensure_ascii=False)
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": f"{system}\nСхема JSON-ответа:\n{schema_json}"},
                     {"role": "user", "content": user}],
        "stream": False,
    }
    if native:
        body.update(format=response_schema if constrain_ollama_schema else "json", think=False,
                    options={"temperature": 0, "num_predict": 1800})
    else:
        body.update(response_format={"type": "json_object"}, temperature=0, max_tokens=1800)
    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
            response = client.post(settings.llm_base_url.rstrip("/") + endpoint,
                                   headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
            message = payload["message"] if native else payload["choices"][0]["message"]
            return schema.model_validate_json(message["content"])
    except (httpx.HTTPError, httpx.InvalidURL):
        raise ModelError("Модель недоступна или отклонила запрос.") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("Модель вернула ответ с неверной структурой.") from None
