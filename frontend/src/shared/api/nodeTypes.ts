import { api } from './client';

export type ParamField = {
  type?: 'string' | 'integer' | 'number' | 'boolean' | 'array';
  title?: string;
  description?: string;
  default?: string | number | boolean;
  const?: string | number | boolean;
  enum?: Array<string | number>;
  anyOf?: Array<{ type?: string }>;
  items?: { type?: string };
  format?: string;
  placeholder?: string;
  minimum?: number;
  maximum?: number;
};

export type ParamSchema = {
  $defs?: Record<string, ParamSchema>;
  type?: 'object';
  properties?: Record<string, ParamField>;
  required?: string[];
};

export type Port = { id: string; type?: string; required?: boolean; multiple?: boolean; accepts?: string[] };

// `kind` is the backend node type (manual_trigger, scan_secrets, ...).
export type NodeTypeDefinition = {
  kind: string;
  label: string;
  description: string;
  category: string;
  params_schema: ParamSchema;
  inputs: Port[];
  outputs: Port[];
};

// Shape of GET /api/check-types items.
type BackendCheckType = {
  type: string;
  label: string;
  description: string;
  inputs: Port[];
  outputs: Port[];
  config_schema: ParamSchema;
};

const CATEGORIES: Record<string, string> = {
  manual_trigger: 'Запуск',
  schedule_trigger: 'Запуск',
  ci_trigger: 'Запуск',
  repository: 'Источники',
  scan_secrets: 'Анализаторы',
  scan_sca: 'Анализаторы',
  scan_sast: 'Анализаторы',
  scan_spring_config: 'Анализаторы',
  scan_password_hashing: 'Анализаторы',
  scan_sensitive_logging: 'Анализаторы',
  merge_findings: 'Логика',
  condition: 'Логика',
  ci_gate: 'Логика',
  ai_report: 'AI',
  notify_telegram: 'Уведомления',
  notify_email: 'Уведомления',
  notify_slack: 'Уведомления',
};

const FIELD_TITLES: Record<string, string> = {
  field: 'Поле сводки',
  operator: 'Операция',
  value: 'Значение',
  channel: 'Канал',
};

export function fromBackend(item: BackendCheckType): NodeTypeDefinition {
  // Condition settings render the leaf comparison schema once per row.
  const schema = item.config_schema.$defs?.ConditionConfig ?? item.config_schema;
  const properties = Object.fromEntries(Object.entries(schema.properties ?? {}).map(
    ([key, field]) => [key, { ...field, title: FIELD_TITLES[key] ?? field.title ?? key }],
  ));
  return {
    kind: item.type,
    label: item.label,
    description: item.description,
    category: CATEGORIES[item.type] ?? 'Прочее',
    params_schema: { ...schema, properties },
    inputs: item.inputs ?? [],
    outputs: item.outputs ?? [],
  };
}

const port = (id: string, type: string, multiple = false): Port => ({ id, type, required: true, multiple });

// Offline fixture that mirrors GET /api/check-types, used only when the backend is unreachable.
const MOCK_CHECK_TYPES: BackendCheckType[] = [
  { type: 'manual_trigger', label: 'Ручной запуск', description: 'Поставить запуск в очередь по кнопке.', inputs: [], outputs: [port('params', 'run_params')], config_schema: {} },
  { type: 'schedule_trigger', label: 'Расписание', description: 'Поставить запуск в очередь по расписанию.', inputs: [], outputs: [port('params', 'run_params')], config_schema: {} },
  { type: 'ci_trigger', label: 'GitHub Actions', description: 'Проверить коммит, полученный из CI.', inputs: [], outputs: [port('params', 'run_params')], config_schema: { properties: {
    branch: { type: 'string', title: 'Ветка', description: 'По умолчанию используется ветка проекта.' },
  } } },
  { type: 'repository', label: 'Репозиторий', description: 'Подготовить исходники и зафиксировать коммит.', inputs: [port('params', 'run_params')], outputs: [port('snapshot', 'snapshot')], config_schema: {} },
  { type: 'scan_secrets', label: 'Secrets', description: 'Найти встроенные секреты.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'scan_sca', label: 'SCA', description: 'Сопоставить зависимости с учебным справочником.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'scan_sast', label: 'SAST', description: 'Проверить Kotlin и конфигурацию безопасности.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'scan_spring_config', label: 'Конфигурация Spring Boot', description: 'Проверить настройки Actuator, CORS и ответов об ошибках.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'scan_password_hashing', label: 'Хеширование паролей', description: 'Найти быстрые хеши паролей в Kotlin.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'scan_sensitive_logging', label: 'Утечки в логи', description: 'Найти передачу паролей, токенов и Authorization в логгер Kotlin.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'merge_findings', label: 'Объединение находок', description: 'Собрать результаты анализаторов.', inputs: [port('results', 'scan_result', true)], outputs: [port('bundle', 'findings_bundle')], config_schema: {} },
  { type: 'ci_gate', label: 'Порог CI', description: 'Вернуть решение в CI и передать его вместе с находками в следующую ноду.', inputs: [port('bundle', 'findings_bundle')], outputs: [port('bundle', 'findings_bundle')], config_schema: { properties: {
    threshold: { type: 'string', title: 'Блокировать начиная с', enum: ['CRITICAL', 'HIGH'], default: 'CRITICAL' },
  } } },
  { type: 'condition', label: 'IF / ELSE', description: 'Выбрать ветку по сводке, покрытию или вердикту CI.', inputs: [port('bundle', 'findings_bundle')], outputs: [port('true', 'findings_bundle'), port('false', 'findings_bundle')], config_schema: { type: 'object', required: ['field', 'operator', 'value'], properties: {
    field: { type: 'string', enum: ['summary.total', 'summary.high_count', 'summary.critical_count', 'coverage.status', 'ci.decision'] },
    operator: { type: 'string', enum: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'matches'] },
    value: { anyOf: [{ type: 'integer' }, { type: 'string' }] },
  } } },
  { type: 'ai_report', label: 'AI-отчёт', description: 'Подготовить рекомендации по сохранённым находкам.', inputs: [port('bundle', 'findings_bundle')], outputs: [port('report', 'report')], config_schema: {} },
  { type: 'notify_telegram', label: 'Telegram', description: 'Разослать отчёт в чаты Telegram.', inputs: [{ ...port('report', 'report'), accepts: ['findings_bundle'] }], outputs: [port('delivery', 'delivery')], config_schema: { type: 'object', required: ['recipients'], properties: {
    use_workspace_settings: { type: 'boolean', title: 'Получатели и реквизиты команды', default: false },
    credential_id: { type: 'string', title: 'Учётные данные' },
    recipients: { type: 'array', items: { type: 'string' }, title: 'Получатели', description: 'chat_id или @username канала, по одному в строке.' },
    summary_only: { type: 'boolean', default: false, title: 'Только краткая сводка и ссылка на отчёт' },
    attach_json: { type: 'boolean', default: true, title: 'Прикладывать JSON-отчёт' },
  } } },
  { type: 'notify_slack', label: 'Slack', description: 'Отправить сводку и ссылку на отчёт в канал команды.', inputs: [{ ...port('report', 'report'), accepts: ['findings_bundle'] }], outputs: [port('delivery', 'delivery')], config_schema: { type: 'object', properties: {} } },
  { type: 'notify_email', label: 'Почта', description: 'Разослать отчёт по электронной почте.', inputs: [{ ...port('report', 'report'), accepts: ['findings_bundle'] }], outputs: [port('delivery', 'delivery')], config_schema: { type: 'object', required: ['recipients'], properties: {
    use_workspace_settings: { type: 'boolean', title: 'Получатели и реквизиты команды', default: false },
    credential_id: { type: 'string', title: 'Учётные данные' },
    recipients: { type: 'array', items: { type: 'string' }, title: 'Получатели', description: 'Адреса почты, по одному в строке.' },
    subject: { type: 'string', title: 'Тема письма', description: 'Если пусто, тема формируется автоматически.' },
    attach_json: { type: 'boolean', default: true, title: 'Прикладывать JSON-отчёт' },
  } } },
];
export const mockNodeTypes: NodeTypeDefinition[] = MOCK_CHECK_TYPES.map(fromBackend);

export async function fetchNodeTypes(signal?: AbortSignal): Promise<NodeTypeDefinition[]> {
  const payload = await api<{ items: BackendCheckType[] }>('/api/check-types', { signal });
  if (!Array.isArray(payload?.items)) throw new Error('Некорректный ответ /api/check-types');
  return payload.items.map(fromBackend);
}
