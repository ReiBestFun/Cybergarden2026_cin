from openai import OpenAI
from config import settings

_client = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        # Инициализация OpenAI-совместимого клиента
        _client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )
    return _client

def chat(messages: list[dict]) -> str:
    """Отправляет запрос к LLM и возвращает сырой текст."""
    response = get_client().chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
    return response.choices[0].message.content