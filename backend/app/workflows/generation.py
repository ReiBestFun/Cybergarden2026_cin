"""Turn a model's interpretation into a graph with known nodes and ports."""

import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from ..config import Settings
from ..llm import complete_json
from ..nodes.catalog import CATALOG, CiGateConfig, ConditionConfig
from ..schemas.contracts import CiThreshold, Edge, Graph, Input, Issue, Name, Node, Position, Ref, ScheduleSettings
from .schedules import spec_from_settings
from .scheduling import ScheduleSpecError
from .validation import validate_graph


class GenerationRequest(Input):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class NotificationPlan(Input):
    channel: Literal["telegram", "email"]
    when: Literal["always", "true", "false"] = "always"
    recipients: list[str] = Field(default_factory=list, max_length=50)


class WorkflowPlan(Input):
    name: Name
    trigger: Literal["manual", "schedule", "ci"]
    scanners: list[Literal["secrets", "sca", "sast"]] = Field(min_length=1, max_length=3)
    schedule: ScheduleSettings | None = None
    ci_branch: Ref | None = None
    ci_threshold: CiThreshold = CiGateConfig().threshold
    condition: ConditionConfig | None = None
    ai_report: bool = False
    notifications: list[NotificationPlan] = Field(default_factory=list, max_length=4)


class ModelAnswer(Input):
    plan: WorkflowPlan | None


class GeneratedWorkflow(Input):
    name: Name
    graph: Graph
    schedule: ScheduleSettings | None
    repository_url: str | None
    issues: list[Issue]


class GenerationError(Exception):
    pass


_SYSTEM_PROMPT = """Ты переводишь описание защитной проверки собственного учебного проекта в план CyberFlow.
Ответь только JSON: {"plan":{"name":"...","trigger":"manual","scanners":["secrets"]}}.
Дай сценарию короткое название на языке пользователя.
Заполняй только поддерживаемые поля. Если запрос нельзя выразить средствами приложения, верни {"plan":null}.
Триггеры: manual — разовый запуск, schedule — периодический, ci — GitHub Actions.
Сканеры: secrets — поиск секретов, sca — зависимости, sast — статический анализ. Если пользователь просит общую проверку, выбери все три.
Для schedule укажи schedule с frequency (daily, weekly, monthly, cron), time в формате HH:MM,
weekdays (0 — понедельник, 6 — воскресенье), day_of_month или cron, смотря какие поля нужны.
Укажи timezone только если пользователь назвал часовой пояс. «По Москве» = Europe/Moscow.
Сохраняй точное время. Если время или нужный день не указаны, оставь соответствующее поле пустым.
Для ci можно указать ci_branch и ci_threshold (HIGH или CRITICAL).
Для условной доставки укажи condition с field, operator и value. Выбирай поля и операции из схемы.
Числовое значение передавай числом. Пример «если есть находки»: summary.total, gt, 0.
Для каждого канала добавь элемент notifications с channel (email или telegram) и when
(always — без условия, true — при выполнении условия, false — иначе).
Если есть условие, примени его к соответствующим уведомлениям. Добавляй ai_report=true только по явной просьбе об AI-пояснении.
Заполняй recipients только явно указанными адресами или Telegram chat_id / @username. Не подставляй выдуманных получателей.
Расположение, связи и адрес репозитория определяет само приложение."""


def _recipient_mentioned(recipient: str, description: str) -> bool:
    if not recipient or len(recipient) > 254 or (recipient.lstrip("-").isdigit() and len(recipient) < 5):
        return False
    return bool(re.search(r"(?<![\w.@+-])" + re.escape(recipient) + r"(?![\w@+-]|\.\w)", description, re.I))


def generate_workflow(data: GenerationRequest, settings: Settings, repository_url: str | None) -> GeneratedWorkflow:
    # Schema describes the model's intent. The application owns the graph topology.
    answer = complete_json(
        settings, ModelAnswer,
        system=_SYSTEM_PROMPT,
        # Compiling the full workflow grammar can terminate an Ollama runner.
        # JSON mode still validates the result against ModelAnswer in complete_json.
        user=data.description, constrain_ollama_schema=False,
    )
    if answer.plan is None:
        raise GenerationError("Опишите учебную проверку, её запуск, сканеры и нужные уведомления.")
    return _build_workflow(answer.plan, data, repository_url)


def _build_workflow(plan: WorkflowPlan, data: GenerationRequest, repository_url: str | None) -> GeneratedWorkflow:
    graph = Graph()
    issues: list[Issue] = []

    def add(kind: str, column: int, row: float = 0, **config) -> str:
        node_id = f"{kind}-{len(graph.nodes) + 1}"
        graph.nodes.append(Node(id=node_id, type=CATALOG[kind].type,
                                position=Position(x=column * 280, y=row * 145), config=config))
        return node_id

    def connect(source: str, source_port: str, target: str, target_port: str) -> None:
        graph.edges.append(Edge(id=f"edge-{len(graph.edges) + 1}", source=source, source_port=source_port,
                                target=target, target_port=target_port))

    trigger_config = {"branch": plan.ci_branch} if plan.trigger == "ci" and plan.ci_branch else {}
    trigger = add(f"{plan.trigger}_trigger", 0, **trigger_config)
    repository = add("repository", 1)
    merged = add("merge_findings", 3)
    connect(trigger, "params", repository, "params")
    scanners = list(dict.fromkeys(plan.scanners))
    for index, kind in enumerate(scanners):
        scanner = add(f"scan_{kind}", 2, index - (len(scanners) - 1) / 2)
        connect(repository, "snapshot", scanner, "snapshot")
        connect(scanner, "result", merged, "results")

    column = 4
    upstream = merged
    if plan.trigger == "ci":
        upstream = add("ci_gate", column, threshold=plan.ci_threshold)
        connect(merged, "bundle", upstream, "bundle")
        column += 1

    outputs = {"always": (upstream, "bundle")}
    branches = {item.when for item in plan.notifications}
    if plan.condition is not None:
        if not branches & {"true", "false"}:
            raise GenerationError("Уточните, какое уведомление отправлять при выполнении условия.")
        if plan.condition.field == "ci.decision" and plan.trigger != "ci":
            raise GenerationError("Условие по вердикту CI требует запуска через GitHub Actions.")
        condition = add("condition", column, **plan.condition.model_dump())
        connect(upstream, "bundle", condition, "bundle")
        outputs.update(true=(condition, "true"), false=(condition, "false"))
        column += 1
    if branches - outputs.keys():
        raise GenerationError("Укажите условие для отправки уведомления.")
    if plan.ai_report:
        if len(branches) > 1:
            raise GenerationError("Для AI-отчёта укажите одну ветку доставки.")
        branch = next(iter(branches), "always")
        ai = add("ai_report", column)
        connect(*outputs[branch], ai, "bundle")
        outputs[branch] = (ai, "report")
        column += 1

    missing_recipients = set()
    for index, item in enumerate(plan.notifications):
        recipients = list(dict.fromkeys(r for r in item.recipients if _recipient_mentioned(r, data.description)))
        kind = f"notify_{item.channel}"
        config = {"recipients": recipients} if recipients else {}
        node = add(kind, column, index - (len(plan.notifications) - 1) / 2, **config)
        if not recipients:
            missing_recipients.add(node)
        connect(*outputs[item.when], node, "report")

    for issue in validate_graph(graph):
        if issue.node_id in missing_recipients and issue.field == "config.recipients":
            label = CATALOG[next(n.type for n in graph.nodes if n.id == issue.node_id)].label
            issues.append(Issue(code="RECIPIENTS_REQUIRED", message=f"Укажите получателей в ноде «{label}».",
                                node_id=issue.node_id, field=issue.field))
        else:
            raise GenerationError(issue.message)

    schedule = None
    if plan.trigger == "schedule":
        schedule = plan.schedule or ScheduleSettings()
        schedule = schedule.model_copy(update={"timezone": schedule.timezone or data.timezone})
        try:
            spec_from_settings(schedule)
        except ScheduleSpecError as error:
            issues.append(Issue(code=error.code, message=error.message, node_id=trigger, field=f"schedule.{error.field}"))

    return GeneratedWorkflow(name=plan.name, graph=graph, repository_url=repository_url, schedule=schedule, issues=issues)
