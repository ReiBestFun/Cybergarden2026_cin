"""Shared provider client for structured model responses."""

import json
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from .config import Settings


class ModelError(Exception):
    pass


def _status_message(status: int) -> str:
    if status in {401, 403}:
        return f"Провайдер отклонил доступ (HTTP {status}). Проверьте API-ключ и права проекта."
    if status == 404:
        return "Провайдер не нашёл модель (HTTP 404). Попробуйте другую модель."
    if status == 429:
        return "Провайдер ограничил запросы (HTTP 429). Проверьте квоту или повторите позже."
    if status >= 500:
        return f"Провайдер временно недоступен (HTTP {status}). Повторите позже или выберите другую модель."
    return f"Провайдер отклонил запрос (HTTP {status})."


def _provider_headers(settings: Settings) -> dict[str, str]:
    try:
        url = urlsplit(settings.llm_base_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError
    except ValueError:
        raise ModelError("Проверьте базовый URL модели в настройках воркера.") from None
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    compose_ollama = (settings.llm_provider == "ollama" and not api_key
                      and settings.llm_base_url.rstrip("/") == "http://ollama:11434")
    if url.scheme != "https" and not compose_ollama and not (
        url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ModelError("Для удалённой модели требуется HTTPS.")
    return {"Authorization": "Bearer " + api_key} if api_key else {}


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
    except httpx.HTTPStatusError as error:
        raise ModelError(_status_message(error.response.status_code)) from None
    except httpx.TimeoutException:
        raise ModelError("Провайдер не ответил вовремя. Повторите запрос.") from None
    except (httpx.RequestError, httpx.InvalidURL):
        raise ModelError("Не удалось подключиться к провайдеру модели.") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("Модель вернула ответ с неверной структурой.") from None
