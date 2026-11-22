"""Read-only starter workflows shared by all workspaces."""

from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from ..schemas.contracts import ScheduleSettings
from ..schemas.templates import TemplateRead
from .generation import WorkflowPlan, build_workflow


def _template(
    slug: str, description: str, plan: WorkflowPlan, *, source: Literal["repository", "local_archive"] = "repository",
) -> TemplateRead:
    workflow = build_workflow(plan, source=source)
    return TemplateRead(
        id=uuid5(NAMESPACE_URL, f"urn:cyberflow:template:{slug}"),
        name=workflow.name, description=description, graph_json=workflow.graph, schedule=workflow.schedule,
        is_builtin=True, can_edit=False, workspace_id=None, draft_source_workspace_id=None,
        created_at=None, updated_at=None,
    )


BUILTIN_TEMPLATES = (
    _template(
        "secrets", "Ищет встроенные пароли, токены и ключи в коде и конфигурации репозитория.",
        WorkflowPlan(name="Быстрый поиск секретов", trigger="manual", scanners=["secrets"]),
    ),
    _template(
        "gradle", "Сверяет прямые Maven-зависимости из Gradle Kotlin DSL со справочником уязвимостей.",
        WorkflowPlan(name="Аудит зависимостей Gradle", trigger="manual", scanners=["sca"]),
    ),
    _template(
        "spring", "Проверяет Kotlin-код, настройки Spring Boot, хеширование паролей и запись чувствительных данных в логи.",
        WorkflowPlan(name="Безопасность Spring Boot", trigger="manual",
                     scanners=["sast", "spring_config", "password_hashing", "sensitive_logging"]),
    ),
    _template(
        "zip", "Запускает все шесть анализаторов по исходникам из ZIP. После копирования выберите файл в ноде «Локальный ZIP».",
        WorkflowPlan(name="Полная проверка ZIP", trigger="manual",
                     scanners=["secrets", "sca", "sast", "spring_config", "password_hashing", "sensitive_logging"]),
        source="local_archive",
    ),
    _template(
        "weekly", "Проверяет секреты, зависимости и Kotlin-код по понедельникам в 09:00 UTC. Включите расписание в редакторе.",
        WorkflowPlan(name="Еженедельный аудит репозитория", trigger="schedule", scanners=["secrets", "sca", "sast"],
                     schedule=ScheduleSettings(frequency="weekly", weekdays=[0], time="09:00", timezone="UTC")),
    ),
    _template(
        "ci", "Проверяет pull request через GitHub Actions в учебном репозитории TBagirov/training. Порог блокировки CI — HIGH.",
        WorkflowPlan(name="Проверка pull request", trigger="ci", scanners=["secrets", "sca", "sast"], ci_threshold="HIGH"),
    ),
)
