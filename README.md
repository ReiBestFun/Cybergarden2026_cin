# AI Security Workflow (CyberFlow)

Визуальный конструктор сценариев проверки безопасности для учебных Kotlin/Spring Boot проектов.

- `backend/`: FastAPI принимает графы, воркер выполняет задания из PostgreSQL, а анализаторы Secrets, SCA и SAST формируют находки и отчёты. [Запуск, Swagger и проверка бэкенда](backend/README.md)
- `frontend/`: редактор сценариев на React, Vite и React Flow. [Запуск фронтенда](frontend/README.md)
- [AI-отчёты и подключение MCP-ассистента](backend/MCP.md)

[Локальная приёмка, отчёты и сценарий защиты](docs/demo/README.md)

## Работа с агентами в Codex

1. Обновите локальную копию репозитория и откройте её корень как проект в Codex. Проектные навыки уже лежат в [`.agents/skills/`](.agents/skills), а инструкции и ссылки для агента — в [`AGENTS.md`](AGENTS.md). Отдельно устанавливать навыки не нужно.
2. Начните новый чат в этом проекте. Наберите `$` и выберите навык из списка. В Codex CLI также можно открыть список командой `/skills`. Если после обновления навык не появился, перезапустите Codex и проверьте, что открыт корень репозитория.
3. Для работы с GitHub Issues установите GitHub CLI, сделайте его доступным в `PATH` и войдите в аккаунт с доступом к проекту. Достаточно проверить команды `gh auth status` и `gh issue list --repo ReiBestFun/Cybergarden2026_cin`. [Установка GitHub CLI](https://cli.github.com/)

| Навык в Codex | Когда использовать |
|---|---|
| `$grill-with-docs` | Разобрать спорные требования и согласовать решения. |
| `$to-spec` | Собрать согласованные решения в спецификацию. |
| `$to-tickets` | Разбить спецификацию на GitHub Issues с критериями и блокерами. |
| `$implement` | Реализовать конкретную задачу. Укажите её номер или ссылку. |
| `$code-review` | Проверить изменения относительно заданной ветки или коммита. |

Пример запроса в Codex:

```text
$implement Выполни https://github.com/ReiBestFun/Cybergarden2026_cin/issues/7
```

Для новой работы выберите открытую задачу без незакрытых блокеров. Перед реализацией прочитайте её критерии приёмки. Если клиент поддерживает команды вида `/implement`, используйте команду этого клиента. В Codex выбирайте проектный навык через `$`.

- [Спецификация](SPEC.md), [глоссарий](GLOSSARY.md) и [порядок работы с GitHub Issues](docs/agents/issue-tracker.md).
- [Проектные навыки Matt Pocock](https://github.com/mattpocock/skills/tree/abeeaf95660ba9e8b7c753eb38f441ca707a8823) и [лицензия MIT](docs/agents/skills-LICENSE).
- [Документация Codex по проектным навыкам](https://learn.chatgpt.com/docs/build-skills).
