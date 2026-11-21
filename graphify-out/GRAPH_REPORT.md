# Graph Report - Cybergarden2026_cin  (2026-11-21)

## Corpus Check
- 86 files · ~40,593 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 899 nodes · 2882 edges · 36 communities
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 406 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ef47aad5`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]

## God Nodes (most connected - your core abstractions)
1. `ApiError` - 80 edges
2. `Warning` - 59 edges
3. `Snapshot` - 52 edges
4. `Settings` - 40 edges
5. `Finding` - 38 edges
6. `DeliveryError` - 35 edges
7. `ScanError` - 34 edges
8. `scan_sast()` - 34 edges
9. `ScanResult` - 31 edges
10. `Scan` - 31 edges

## Surprising Connections (you probably didn't know these)
- `Graph` --uses--> `ApiError`  [INFERRED]
  backend/app/api/workflows.py → backend/app/api/common.py
- `PageQuery` --uses--> `ApiError`  [INFERRED]
  backend/app/api/workflows.py → backend/app/api/common.py
- `CatalogRead` --uses--> `ApiError`  [INFERRED]
  backend/app/api/workflows.py → backend/app/api/common.py
- `SchedulePreview` --uses--> `ApiError`  [INFERRED]
  backend/app/api/workflows.py → backend/app/api/common.py
- `ScheduleUpdate` --uses--> `ApiError`  [INFERRED]
  backend/app/api/workflows.py → backend/app/api/common.py

## Import Cycles
- 1-file cycle: `backend/app/api/runs.py -> backend/app/api/runs.py`
- 1-file cycle: `backend/app/worker.py -> backend/app/worker.py`
- 1-file cycle: `backend/app/api/ci.py -> backend/app/api/ci.py`
- 1-file cycle: `backend/app/api/projects.py -> backend/app/api/projects.py`
- 1-file cycle: `backend/app/main.py -> backend/app/main.py`
- 1-file cycle: `backend/app/integrations/github_actions.py -> backend/app/integrations/github_actions.py`
- 1-file cycle: `backend/app/workflows/queue.py -> backend/app/workflows/queue.py`
- 1-file cycle: `backend/app/api/templates.py -> backend/app/api/templates.py`
- 1-file cycle: `backend/app/api/workflows.py -> backend/app/api/workflows.py`
- 1-file cycle: `backend/app/notifications/queue.py -> backend/app/notifications/queue.py`
- 1-file cycle: `backend/app/workflows/schedules.py -> backend/app/workflows/schedules.py`
- 1-file cycle: `backend/app/workflows/scheduling.py -> backend/app/workflows/scheduling.py`
- 2-file cycle: `backend/app/api/common.py -> backend/app/main.py -> backend/app/api/common.py`
- 2-file cycle: `backend/app/db/session.py -> backend/app/main.py -> backend/app/db/session.py`

## Communities (36 total, 0 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (98): Finding, finding_key(), Shared input and output for the static analyzers., Collect findings and diagnostics for one analyzer invocation., Scan, ScanError, ScanResult, Snapshot (+90 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (70): Settings, create_app(), _heartbeat(), main(), Consume queued workflow runs from PostgreSQL., run_next(), tick_schedules(), RunnerMode (+62 more)

### Community 2 - "Community 2"
Cohesion: 0.08
Nodes (64): main(), process_next(), Deliver reports queued by the workflow worker., _send(), Attachment, Session, sessionmaker, Settings (+56 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (34): PurePosixPath, Allowlist, Finding, Node, NodeResult, RunProgress, RunReport, Settings (+26 more)

### Community 4 - "Community 4"
Cohesion: 0.13
Nodes (39): datetime, Schedule, ScheduleRead, ScheduleSettings, datetime, date, ScheduleSpec, test_daily_schedule_skips_nonexistent_local_time() (+31 more)

### Community 5 - "Community 5"
Cohesion: 0.15
Nodes (36): _authorize(), _check_repository(), _ci_graph(), CiTestRequest, download_workflow(), get_ci_run(), issue_token(), start_ci_run() (+28 more)

### Community 6 - "Community 6"
Cohesion: 0.12
Nodes (22): NodeStatus, Schedule, browserTimeZone(), ciPreset, defaultSchedule(), Draft, getSaved(), initialEdges (+14 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (42): BaseModel, CatalogRead, CiGateConfig, CiTriggerConfig, ConditionConfig, ConditionDraft, definition(), EmailConfig (+34 more)

### Community 8 - "Community 8"
Cohesion: 0.15
Nodes (28): graph_error(), create_workflow(), _draft(), get_schedule(), get_workflow(), list_node_types(), list_workflows(), modify_workflow() (+20 more)

### Community 9 - "Community 9"
Cohesion: 0.17
Nodes (30): Coverage, Pagination, create_run(), export_run(), FindingFilters, _findings(), get_report(), _get_run() (+22 more)

### Community 10 - "Community 10"
Cohesion: 0.20
Nodes (14): errorMessage(), getSecurityWorkflow(), Canvas(), CiSetup(), Editor(), CustomNode(), nodeTypes, STATUS_ICON (+6 more)

### Community 11 - "Community 11"
Cohesion: 0.15
Nodes (32): safe_path(), accepts_sast_source(), _cell(), merge_findings(), NodeResult, Build machine-readable and human-readable reports from the same findings., readable_report(), RunReport (+24 more)

### Community 12 - "Community 12"
Cohesion: 0.10
Nodes (18): copyTemplate(), listTemplates(), publishTemplate(), WorkflowTemplate, ApiGraph, listRuns(), listWorkflows(), Page (+10 more)

### Community 13 - "Community 13"
Cohesion: 0.14
Nodes (16): BackendCheckType, CATEGORIES, fetchNodeTypes(), FIELD_TITLES, MOCK_CHECK_TYPES, mockNodeTypes, NodeTypeDefinition, ParamField (+8 more)

### Community 14 - "Community 14"
Cohesion: 0.09
Nodes (21): dependencies, lucide-react, react, react-dom, react-router-dom, @xyflow/react, zustand, devDependencies (+13 more)

### Community 15 - "Community 15"
Cohesion: 0.08
Nodes (31): watchRun(), createWorkflow(), exportUrl(), Finding, getReport(), getRun(), getSchedule(), getWorkflow() (+23 more)

### Community 16 - "Community 16"
Cohesion: 0.24
Nodes (12): page(), Session, Graph, Issue, Node, Error, Issue, T (+4 more)

### Community 17 - "Community 17"
Cohesion: 0.32
Nodes (14): Call, child(), children(), end_line(), KotlinFile, line(), literal(), operator() (+6 more)

### Community 18 - "Community 18"
Cohesion: 0.31
Nodes (7): api(), API_BASE, ApiError, ApiErrorBody, apiFile(), apiUrl(), request()

### Community 19 - "Community 19"
Cohesion: 0.12
Nodes (14): AI Security Workflow, CI/CD через GitHub Actions, Бэкенд и PostgreSQL, Локальный запуск анализаторов, Оповещения по почте и в Telegram, Подключение фронтенда, Проверка, Расписание (+6 more)

### Community 20 - "Community 20"
Cohesion: 0.13
Nodes (14): compilerOptions, allowImportingTsExtensions, isolatedModules, jsx, lib, module, moduleResolution, noEmit (+6 more)

### Community 21 - "Community 21"
Cohesion: 0.19
Nodes (12): Frequency, previewSchedule(), CRON_PRESETS, dayKey(), DAYS, DAYS_FULL, describe(), formatSlot() (+4 more)

### Community 22 - "Community 22"
Cohesion: 0.29
Nodes (5): Request, Session, get_session(), FastAPI, ErrorResponse

### Community 23 - "Community 23"
Cohesion: 0.49
Nodes (10): copy_template(), list_templates(), publish_template(), Database, PageQuery, UUID, Workflow, TemplateCopy (+2 more)

### Community 31 - "Community 31"
Cohesion: 0.19
Nodes (24): Graph, Project, ScanRun, Schedule, Session, Workflow, Base, Credential (+16 more)

### Community 32 - "Community 32"
Cohesion: 0.15
Nodes (13): ParamSchema, canonicalRepo(), ensureProject(), getProject(), listProjects(), NodeRun, CredentialPicker(), messages (+5 more)

### Community 33 - "Community 33"
Cohesion: 0.16
Nodes (7): BaseHTTPRequestHandler, _Handler, main(), Local SMTP sink for development: accepts every message and saves it as an .eml f, SmtpSink, Received, _FakeTelegram

## Knowledge Gaps
- **93 isolated node(s):** `Path`, `Category`, `Session`, `T`, `Issue` (+88 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ApiError` connect `Community 5` to `Community 1`, `Community 2`, `Community 7`, `Community 8`, `Community 9`, `Community 16`, `Community 22`, `Community 23`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `Warning` connect `Community 0` to `Community 1`, `Community 3`, `Community 7`, `Community 9`, `Community 11`, `Community 31`?**
  _High betweenness centrality (0.060) - this node is a cross-community bridge._
- **Why does `Snapshot` connect `Community 0` to `Community 1`, `Community 2`, `Community 3`, `Community 33`, `Community 11`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Are the 58 inferred relationships involving `ApiError` (e.g. with `CiTestRequest` and `FindingFilters`) actually correct?**
  _`ApiError` has 58 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `Warning` (e.g. with `AccessRule` and `Function`) actually correct?**
  _`Warning` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `Snapshot` (e.g. with `AccessRule` and `Function`) actually correct?**
  _`Snapshot` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 15 inferred relationships involving `Settings` (e.g. with `Settings` and `Session`) actually correct?**
  _`Settings` has 15 INFERRED edges - model-reasoned connections that need verification._