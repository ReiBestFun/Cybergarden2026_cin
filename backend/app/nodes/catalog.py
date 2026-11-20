from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..schemas.contracts import Input

FIELD = Literal["summary.total", "summary.high_count", "summary.critical_count", "coverage.status"]
OPERATOR = Literal["eq", "ne", "gt", "gte", "lt", "lte", "matches"]
OPERAND = Annotated[int, Field(strict=True)] | Annotated[str, Field(max_length=256)]


class ConditionConfig(Input):
    field: FIELD
    operator: OPERATOR
    value: OPERAND


class ConditionDraft(Input):
    field: FIELD | None = None
    operator: OPERATOR | None = None
    value: OPERAND | None = None


class TelegramConfig(Input):
    channel: Literal["default"] = "default"


class Port(BaseModel):
    id: str
    type: str
    required: bool = True
    multiple: bool = False


class NodeDefinition(BaseModel):
    type: str
    label: str
    description: str
    inputs: list[Port]
    outputs: list[Port]
    config_schema: dict


def definition(
    kind: str,
    label: str,
    description: str,
    inputs: tuple[Port, ...],
    outputs: tuple[Port, ...],
    config: type[Input] = Input,
) -> NodeDefinition:
    return NodeDefinition(
        type=kind, label=label, description=description, inputs=list(inputs), outputs=list(outputs),
        config_schema=config.model_json_schema(),
    )


PARAMS = Port(id="params", type="run_params")
SNAPSHOT = Port(id="snapshot", type="snapshot")
RESULT = Port(id="result", type="scan_result")
BUNDLE = Port(id="bundle", type="findings_bundle")
REPORT = Port(id="report", type="report")

CATALOG = {node.type: node for node in (
    definition("manual_trigger", "Ручной запуск", "Поставить запуск в очередь по кнопке.", (), (PARAMS,)),
    definition("schedule_trigger", "Расписание", "Поставить запуск в очередь по расписанию.", (), (PARAMS,)),
    definition("repository", "Репозиторий", "Подготовить исходники и зафиксировать коммит.", (PARAMS,), (SNAPSHOT,)),
    definition("scan_secrets", "Secrets", "Найти встроенные секреты.", (SNAPSHOT,), (RESULT,)),
    definition("scan_sca", "SCA", "Сопоставить зависимости с учебным справочником.", (SNAPSHOT,), (RESULT,)),
    definition("scan_sast", "SAST", "Проверить Kotlin и конфигурацию безопасности.", (SNAPSHOT,), (RESULT,)),
    definition("merge_findings", "Объединение находок", "Собрать результаты анализаторов.",
               (Port(id="results", type="scan_result", multiple=True),), (BUNDLE,)),
    definition("condition", "IF / ELSE", "Выбрать ветку по сводке или покрытию.", (BUNDLE,),
               (Port(id="true", type=BUNDLE.type), Port(id="false", type=BUNDLE.type)), ConditionConfig),
    definition("ai_report", "AI-отчёт", "Подготовить рекомендации по сохранённым находкам.", (BUNDLE,), (REPORT,)),
    definition("notify_telegram", "Telegram", "Отправить сводку в настроенный канал.",
               (REPORT,), (Port(id="delivery", type="delivery"),), TelegramConfig),
)}


class CatalogRead(BaseModel):
    items: list[NodeDefinition]
