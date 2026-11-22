from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, RootModel, StringConstraints, ValidationInfo, field_validator

from ..notifications.email import ADDRESS
from ..notifications.telegram import CHAT_ID

from ..schemas.contracts import CiThreshold, Input, Ref

FIELD = Literal["summary.total", "summary.high_count", "summary.critical_count", "coverage.status", "ci.decision"]
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


class ConditionGroup(Input):
    mode: Literal["AND", "OR"]
    conditions: list[ConditionConfig] = Field(min_length=2)


ConditionExpression = RootModel[ConditionConfig | ConditionGroup]


class CiGateConfig(Input):
    threshold: CiThreshold = Field(default="CRITICAL", title="Блокировать начиная с")


class CiTriggerConfig(Input):
    branch: Ref | None = Field(default=None, title="Ветка", description="По умолчанию используется ветка проекта.")


ChatId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=CHAT_ID.pattern)]
EmailAddress = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254, pattern=ADDRESS.pattern)]


class NotificationConfig(Input):
    use_workspace_settings: bool = Field(default=False, title="Получатели и реквизиты команды")
    credential_id: UUID | None = Field(default=None, title="Учётные данные")
    attach_json: bool = Field(default=True, title="Прикладывать JSON-отчёт")

    @field_validator("recipients", mode="before", check_fields=False)
    @classmethod
    def ignore_manual_recipients(cls, recipients: object, info: ValidationInfo) -> object:
        return [] if info.data.get("use_workspace_settings") else recipients

    @field_validator("recipients", check_fields=False)
    @classmethod
    def require_recipients(cls, recipients: list[str], info: ValidationInfo) -> list[str]:
        if not recipients and not info.data.get("use_workspace_settings"):
            raise ValueError("Укажите получателей уведомления.")
        return recipients


class TelegramConfig(NotificationConfig):
    summary_only: bool = Field(default=False, title="Только краткая сводка и ссылка на отчёт")
    recipients: list[ChatId] = Field(
        default_factory=list, validate_default=True, max_length=50, title="Получатели",
        description="chat_id или @username канала, по одному в строке. Бот должен быть добавлен в чат.",
    )


class EmailConfig(NotificationConfig):
    recipients: list[EmailAddress] = Field(
        default_factory=list, validate_default=True, max_length=50, title="Получатели", description="Адреса почты, по одному в строке.",
    )
    subject: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200, pattern=r"^[^\r\n]*$")] | None = Field(
        default=None, title="Тема письма", description="Если пусто, тема формируется автоматически.",
    )


NOTIFIER_CONFIGS: dict[str, type[TelegramConfig | EmailConfig]] = {"notify_telegram": TelegramConfig, "notify_email": EmailConfig}


class Port(BaseModel):
    id: str
    type: str
    required: bool = True
    multiple: bool = False
    # Additional source port types this input can be connected to.
    accepts: list[str] = Field(default_factory=list)

    def compatible(self, source_type: str) -> bool:
        return source_type == self.type or source_type in self.accepts


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
    config: type[BaseModel] = Input,
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
# Notifications take the merged findings directly or, later, the AI report built from them.
NOTIFY_INPUT = Port(id="report", type="report", accepts=["findings_bundle"])
DELIVERY = Port(id="delivery", type="delivery")

CATALOG = {node.type: node for node in (
    definition("manual_trigger", "Ручной запуск", "Поставить запуск в очередь по кнопке.", (), (PARAMS,)),
    definition("schedule_trigger", "Расписание", "Поставить запуск в очередь по расписанию.", (), (PARAMS,)),
    definition("ci_trigger", "GitHub Actions", "Проверить коммит, полученный из CI.", (), (PARAMS,), CiTriggerConfig),
    definition("repository", "Репозиторий", "Подготовить исходники и зафиксировать коммит.", (PARAMS,), (SNAPSHOT,)),
    definition("scan_secrets", "Secrets", "Найти встроенные секреты.", (SNAPSHOT,), (RESULT,)),
    definition("scan_sca", "SCA", "Сопоставить зависимости с учебным справочником.", (SNAPSHOT,), (RESULT,)),
    definition("scan_sast", "SAST", "Проверить Kotlin и конфигурацию безопасности.", (SNAPSHOT,), (RESULT,)),
    definition("scan_spring_config", "Конфигурация Spring Boot", "Проверить настройки Actuator, CORS и ответов об ошибках.", (SNAPSHOT,), (RESULT,)),
    definition("scan_password_hashing", "Хеширование паролей", "Найти быстрые хеши паролей в Kotlin.", (SNAPSHOT,), (RESULT,)),
    definition("scan_sensitive_logging", "Утечки в логи", "Найти передачу паролей, токенов и Authorization в логгер Kotlin.", (SNAPSHOT,), (RESULT,)),
    definition("merge_findings", "Объединение находок", "Собрать результаты анализаторов.",
               (Port(id="results", type="scan_result", multiple=True),), (BUNDLE,)),
    definition("ci_gate", "Порог CI", "Вернуть решение в CI и передать его вместе с находками в следующую ноду.",
               (BUNDLE,), (BUNDLE,), CiGateConfig),
    definition("condition", "IF / ELSE", "Выбрать ветку по сводке, покрытию или вердикту CI.", (BUNDLE,),
               (Port(id="true", type=BUNDLE.type), Port(id="false", type=BUNDLE.type)), ConditionExpression),
    definition("ai_report", "AI-отчёт", "Подготовить рекомендации по сохранённым находкам.", (BUNDLE,), (REPORT,)),
    definition("notify_telegram", "Telegram", "Разослать отчёт в чаты Telegram.",
               (NOTIFY_INPUT,), (DELIVERY,), TelegramConfig),
    definition("notify_email", "Почта", "Разослать отчёт по электронной почте.",
               (NOTIFY_INPUT,), (DELIVERY,), EmailConfig),
)}


class CatalogRead(BaseModel):
    items: list[NodeDefinition]
