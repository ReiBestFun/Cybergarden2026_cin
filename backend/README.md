# AI Security Workflow

Модуль статического анализа запускает три проверки на одном локальном снимке учебного проекта Kotlin/Spring Boot. На выходе он формирует JSON для сдачи и читаемый Markdown-отчёт с покрытием каждой проверки.

## Бэкенд и PostgreSQL

Из корня проекта с Python 3.12+ выполните команды ниже. Для локальной разработки можно использовать значения из .env.example.

    python -m venv .venv
    .venv/bin/python -m pip install -r backend/requirements.txt
    docker compose up -d --wait db
    .venv/bin/alembic -c backend/alembic.ini upgrade head
    .venv/bin/uvicorn backend.app.main:app --reload

Swagger доступен по адресу http://127.0.0.1:8000/docs. Маршрут /api/health проверяет подключение к БД. При использовании установленного PostgreSQL задайте DATABASE_URL в окружении или в .env и примените ту же миграцию.

Во втором терминале из корня проекта запустите воркер с тем же DATABASE_URL:

    .venv/bin/python -m backend.app.queue_worker

Параметр --once позволяет обработать одно доступное задание и завершиться. Воркер забирает задания из PostgreSQL, получает разрешённый репозиторий, выполняет сохранённый граф и записывает находки и статусы. Для открытого учебного набора SCA берёт справочник из репозитория. Для другого разрешённого учебного источника укажите SCA_CATALOG_PATH.

Для проверки из Swagger создайте сценарий manual_trigger → repository → scan_secrets → merge_findings и отправьте POST /api/workflows/{workflow_id}/runs. Ответ 202 содержит ID запуска. GET /api/runs/{run_id} показывает переход из queued в итоговый статус, GET /api/runs/{run_id}/findings отдаёт найденные проблемы, GET /api/runs/{run_id}/report показывает покрытие. JSON и Markdown доступны через GET /api/runs/{run_id}/export с параметром format=json или format=markdown.

Расписание можно настроить в API. Автоматическая постановка плановых слотов, LLM и отправка в Telegram относятся к следующим этапам.

### Подключение фронтенда

Ветка [frontend](https://github.com/ReiBestFun/Cybergarden2026_cin/tree/frontend) пока использует localStorage и демонстрационный запуск. CORS разрешает локальному Vite обращаться к API с порта 5173.

- Загружайте палитру, параметры и идентификаторы портов через GET /api/check-types. Передавайте Graph в API с полями id, type, config, position у нод и id, source, source_port, target, target_port у рёбер.
- Преобразуйте data.kind React Flow в серверные типы. Для manual, schedule, secrets, sca, sast, ai, telegram используются manual_trigger, schedule_trigger, scan_secrets, scan_sca, scan_sast, ai_report, notify_telegram. Тип condition совпадает. Для исполняемого графа добавьте repository перед анализаторами и merge_findings после них. Сохраняйте sourceHandle и targetHandle как source_port и target_port.
- Настраивайте расписание отдельным PUT /api/workflows/{workflow_id}/schedule с ежедневным временем и часовым поясом. Настройки condition передавайте как field, operator, value. Канал Telegram задаётся на сервере.
- Для запуска вызовите POST /api/workflows/{workflow_id}/runs, затем опрашивайте GET /api/runs/{run_id}. Для списка находок используйте GET /api/runs/{run_id}/findings, для сводки — GET /api/runs/{run_id}/report.

## Локальный запуск анализаторов

Выполните команды из корня проекта. Подставьте полный SHA снимка в переменную TRAINING_COMMIT.

    .venv/bin/python -m pip install -r backend/requirements.txt
    git clone https://github.com/TBagirov/training.git .venv/training
    TRAINING_COMMIT=$(git -C .venv/training rev-parse HEAD)
    .venv/bin/python -m backend.app.worker .venv/training/training-project \
      --sha "$TRAINING_COMMIT" \
      --catalog .venv/training/vulnerability-database/vulnerabilities.json \
      --json-out findings.json \
      --markdown-out report.md

Без параметра --markdown-out читаемый отчёт выводится в терминал. Код выхода 0 означает, что все проверки завершились в рамках заявленного покрытия, 1 означает частичное покрытие или сбой одной из проверок с сохранением доступных результатов, 2 означает, что проверки не дали результата или возникла ошибка записи отчёта. Параметр --timeout задаёт лимит на один тестер в секундах.

Команда принимает заранее подготовленный локальный снимок. Secrets проверяет исходники Kotlin и текстовую конфигурацию. SCA сопоставляет прямые объявления Gradle Kotlin DSL с учебным справочником, включая версии из lockfile при совпадении конфигурации. SAST проверяет поддерживаемые шаблоны Kotlin, JDBC и Spring Security. При частичном покрытии причина указана в Markdown.

## Проверка

    .venv/bin/python -m pip install -r backend/requirements-dev.txt
    TRAINING_DATASET=.venv/training .venv/bin/python -m pytest -q backend/tests

Переменная TRAINING_DATASET указывает на корень учебного набора. Интеграционный тест сверяет JSON с эталоном и проверяет, что участки из отрицательных контролей не попали в отчёт. Без этой переменной выполняются синтетические тесты.

Для проверки API и очереди задайте TEST_DATABASE_URL отдельной тестовой базы PostgreSQL. Тесты применят к ней миграцию, создадут тестовые запуски и проверят сохранение результатов воркера, частичный отчёт, отказ источника и захват задания двумя транзакциями.
