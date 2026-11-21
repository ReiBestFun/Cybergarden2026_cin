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

### Расписание

PUT /api/workflows/{workflow_id}/schedule включает расписание для сценария с триггером schedule_trigger. Поле `frequency` задаёт периодичность:

| frequency | Поля | Пример |
|---|---|---|
| `daily` | `time` | каждый день в 09:00 |
| `weekly` | `time`, `weekdays` (0 = пн … 6 = вс) | по пн и чт в 10:00 |
| `monthly` | `time`, `day_of_month` (1–31; в коротких месяцах — последний день) | 1-го числа в 03:00 |
| `cron` | `cron` (5 полей, `*`, списки, диапазоны, шаги, имена MON/JAN) | `0 9 * * 1-5` |

Для всех режимов обязателен `timezone` в формате IANA. POST /api/schedules/preview с теми же полями возвращает ближайшие пять запусков, на нём построен предпросмотр в редакторе.

Плановые запуски ставит сам воркер: раз в `SCHEDULER_POLL_SECONDS` (по умолчанию 15 с) он забирает наступившие слоты с `FOR UPDATE SKIP LOCKED` и создаёт запуск с `trigger=schedule`. Уникальность пары «расписание + слот» не даёт создать дубль, даже если воркеров несколько. Если воркер был выключен и пропустил слоты, создаётся один запуск, после чего расписание переходит к следующему будущему слоту. Если граф сломан, запуск не создаётся, а причина записывается в `last_error`. Браузер для работы расписания не нужен.

Кнопка «Запустить» работает для сценария с любым триггером, в том числе с расписанием.

### CI/CD через GitHub Actions

1. Примените миграции и запустите API, воркер и фронтенд. На странице «Мои сценарии» откройте «Готовый сценарий CI/CD». Он настроен на `TBagirov/training`, три анализатора и порог `CRITICAL`.
2. Нажмите «Проверить коммит в CI». Редактор сохранит сценарий и поставит в очередь проверку указанного SHA. Итог и ссылка на отчёт появятся после завершения. Для CI действует `ALLOWED_REPOSITORIES` даже при включённом `ALLOW_ANY_REPOSITORY`.
3. Для автоматического запуска откройте «Подключить автоматический запуск из GitHub» в редакторе. Выпустите токен и сохраните его в GitHub Actions Secrets под именем `CYBERFLOW_TOKEN`. Скопируйте готовый `security.yml` в `.github/workflows/security.yml` через интерфейс GitHub. Токен хранится на сервере в виде хеша. Новый токен заменяет предыдущий. Токены из `CI_WORKFLOW_TOKENS` остаются действительными для сценариев, которые ещё не получили токен через редактор.
4. Для реальных пушей с локальным API настройте изолированный Windows self-hosted runner с меткой `cyberflow` на машине с CyberFlow. Установка runner и workflow требует прав на настройки учебного репозитория.

Для HTTP-проверки отправьте `POST /api/hooks/<workflow_id>` с заголовком `Authorization: Bearer <токен>` и JSON вида `{"repository_url":"https://github.com/TBagirov/training","commit_sha":"<полный SHA>","request_id":"local-demo-1"}`. Затем запросите `GET /api/hooks/<workflow_id>/runs/<id>` с тем же заголовком. Повтор запроса с тем же `request_id` и SHA вернёт существующий запуск.

В GitHub job успешен при `passed`. Подтверждённая находка от заданного порога даёт `blocked`, даже если часть анализа не завершилась. При сбое или неполном покрытии без такой находки итог `error`. По умолчанию порог `CRITICAL`, в ноде можно выбрать `HIGH`. Настройка `.env` `APP_BASE_URL` определяет адрес ссылки на отчёт. Загрузка исходников использует существующий HTTPS-доступ к учебному репозиторию.

После деплоя выберите «GitHub-hosted · HTTPS» в редакторе и задайте переменную Actions `CYBERFLOW_API_URL` с HTTPS-адресом. Для доступа runner достаточно опубликовать маршруты `/api/hooks/…`. Редактор, выпуск токенов и отчёты должны оставаться за вашим обычным контуром доступа.

AI-отчёт пока не выполняется воркером.

### Подключение фронтенда

Фронтенд лежит в [frontend/](../frontend/README.md) и работает с этим API напрямую. В разработке Vite проксирует `/api` на http://127.0.0.1:8000, поэтому CORS не нужен.

- Палитра, параметры и порты нод берутся из GET /api/check-types. В React Flow `data.kind` совпадает с серверным типом ноды, а `sourceHandle` и `targetHandle` с `source_port` и `target_port`.
- «Сохранить» находит или создаёт проект по ссылке на репозиторий и вызывает POST /api/projects/{project_id}/workflows или PATCH /api/workflows/{workflow_id}. Для ноды schedule_trigger время уходит в PUT /api/workflows/{workflow_id}/schedule.
- «Запустить» сохраняет граф, проверяет его через POST /api/workflows/{workflow_id}/validate, ставит запуск POST /api/workflows/{workflow_id}/runs и раз в секунду опрашивает GET /api/runs/{run_id}, раскрашивая ноды по статусам.
- Страница результатов читает GET /api/runs/{run_id} и /findings, экспорт идёт через /export. История и список сценариев собираются из /api/projects, /runs и /workflows.

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
