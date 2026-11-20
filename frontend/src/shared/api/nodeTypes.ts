import { api } from './client';

export type ParamField = {
  type?: 'string' | 'integer' | 'number' | 'boolean';
  title?: string;
  description?: string;
  default?: string | number | boolean;
  const?: string | number | boolean;
  enum?: Array<string | number>;
  anyOf?: Array<{ type?: string }>;
  format?: string;
  placeholder?: string;
  minimum?: number;
  maximum?: number;
};

export type ParamSchema = {
  type?: 'object';
  properties?: Record<string, ParamField>;
  required?: string[];
};

export type Port = { id: string; type?: string; required?: boolean; multiple?: boolean };

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
  repository: 'Источники',
  scan_secrets: 'Анализаторы',
  scan_sca: 'Анализаторы',
  scan_sast: 'Анализаторы',
  merge_findings: 'Логика',
  condition: 'Логика',
  ai_report: 'AI',
  notify_telegram: 'Уведомления',
};

const FIELD_TITLES: Record<string, string> = {
  field: 'Поле сводки',
  operator: 'Операция',
  value: 'Значение',
  channel: 'Канал',
};

export function fromBackend(item: BackendCheckType): NodeTypeDefinition {
  const properties = Object.fromEntries(Object.entries(item.config_schema?.properties ?? {}).map(
    ([key, field]) => [key, { ...field, title: FIELD_TITLES[key] ?? field.title ?? key }],
  ));
  return {
    kind: item.type,
    label: item.label,
    description: item.description,
    category: CATEGORIES[item.type] ?? 'Прочее',
    params_schema: { ...item.config_schema, properties },
    inputs: item.inputs ?? [],
    outputs: item.outputs ?? [],
  };
}

const port = (id: string, type: string, multiple = false): Port => ({ id, type, required: true, multiple });

// Offline fixture that mirrors GET /api/check-types, used only when the backend is unreachable.
const MOCK_CHECK_TYPES: BackendCheckType[] = [
  { type: 'manual_trigger', label: 'Ручной запуск', description: 'Поставить запуск в очередь по кнопке.', inputs: [], outputs: [port('params', 'run_params')], config_schema: {} },
  { type: 'schedule_trigger', label: 'Расписание', description: 'Поставить запуск в очередь по расписанию.', inputs: [], outputs: [port('params', 'run_params')], config_schema: {} },
  { type: 'repository', label: 'Репозиторий', description: 'Подготовить исходники и зафиксировать коммит.', inputs: [port('params', 'run_params')], outputs: [port('snapshot', 'snapshot')], config_schema: {} },
  { type: 'scan_secrets', label: 'Secrets', description: 'Найти встроенные секреты.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'scan_sca', label: 'SCA', description: 'Сопоставить зависимости с учебным справочником.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'scan_sast', label: 'SAST', description: 'Проверить Kotlin и конфигурацию безопасности.', inputs: [port('snapshot', 'snapshot')], outputs: [port('result', 'scan_result')], config_schema: {} },
  { type: 'merge_findings', label: 'Объединение находок', description: 'Собрать результаты анализаторов.', inputs: [port('results', 'scan_result', true)], outputs: [port('bundle', 'findings_bundle')], config_schema: {} },
  { type: 'condition', label: 'IF / ELSE', description: 'Выбрать ветку по сводке или покрытию.', inputs: [port('bundle', 'findings_bundle')], outputs: [port('true', 'findings_bundle'), port('false', 'findings_bundle')], config_schema: { type: 'object', required: ['field', 'operator', 'value'], properties: {
    field: { type: 'string', enum: ['summary.total', 'summary.high_count', 'summary.critical_count', 'coverage.status'] },
    operator: { type: 'string', enum: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'matches'] },
    value: { anyOf: [{ type: 'integer' }, { type: 'string' }] },
  } } },
  { type: 'ai_report', label: 'AI-отчёт', description: 'Подготовить рекомендации по сохранённым находкам.', inputs: [port('bundle', 'findings_bundle')], outputs: [port('report', 'report')], config_schema: {} },
  { type: 'notify_telegram', label: 'Telegram', description: 'Отправить сводку в настроенный канал.', inputs: [port('report', 'report')], outputs: [port('delivery', 'delivery')], config_schema: { type: 'object', properties: { channel: { type: 'string', const: 'default', default: 'default' } } } },
];
export const mockNodeTypes: NodeTypeDefinition[] = MOCK_CHECK_TYPES.map(fromBackend);

export async function fetchNodeTypes(signal?: AbortSignal): Promise<NodeTypeDefinition[]> {
  const payload = await api<{ items: BackendCheckType[] }>('/api/check-types', { signal });
  if (!Array.isArray(payload?.items)) throw new Error('Некорректный ответ /api/check-types');
  return payload.items.map(fromBackend);
}
