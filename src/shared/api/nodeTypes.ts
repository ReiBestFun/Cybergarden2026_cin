export type ParamSchema = {
  type?: 'object';
  properties?: Record<string, {
    type?: 'string' | 'integer' | 'number' | 'boolean';
    title?: string;
    description?: string;
    default?: string | number | boolean;
    enum?: Array<string | number>;
    format?: string;
    placeholder?: string;
    minimum?: number;
    maximum?: number;
  }>;
  required?: string[];
};

export type NodeTypeDefinition = {
  kind: string;
  label: string;
  description: string;
  category: string;
  params_schema: ParamSchema;
  inputs?: Array<{ id: string; type?: string }>;
  outputs?: Array<{ id: string; type?: string }>;
};

// This is a development fixture, not a second hard-coded UI catalog.
// Backend should return this exact array shape from GET /node-types.
export const mockNodeTypes: NodeTypeDefinition[] = [
  { kind: 'manual', label: 'Ручной запуск', description: 'Запуск по кнопке', category: 'Запуск', params_schema: { type: 'object', properties: {} }, inputs: [], outputs: [{id:'out'}] },
  { kind: 'schedule', label: 'Расписание', description: 'Периодический запуск', category: 'Запуск', params_schema: { type: 'object', properties: { cron: { type: 'string', title: 'Cron-выражение', placeholder: '0 14 * * *' } }, required: ['cron'] }, inputs: [], outputs: [{id:'out'}] },
  { kind: 'webhook', label: 'Webhook Trigger', description: 'Запуск по HTTP-запросу', category: 'Запуск', params_schema: { type: 'object', properties: { path: { type: 'string', title: 'Webhook path', placeholder: '/hooks/security' } } }, inputs: [], outputs: [{id:'out'}] },
  { kind: 'git', label: 'Git Repository', description: 'Загрузка исходного кода', category: 'Источники', params_schema: { type: 'object', properties: { url: { type: 'string', title: 'URL репозитория', placeholder: 'https://github.com/TBagirov/training' }, ref: { type: 'string', title: 'Ветка или commit', default: 'main' } }, required: ['url'] }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'upload_zip', label: 'ZIP Archive', description: 'Загруженный архив проекта', category: 'Источники', params_schema: { type: 'object', properties: { artifact_id: { type: 'string', title: 'ID загруженного архива' } }, required: ['artifact_id'] }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'secrets', label: 'Secrets Scanner', description: 'Поиск секретов', category: 'Безопасность', params_schema: { type: 'object', properties: { path: { type: 'string', title: 'Путь внутри проекта', placeholder: 'src/main' } } }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'sca', label: 'SCA Scanner', description: 'Проверка зависимостей', category: 'Безопасность', params_schema: { type: 'object', properties: { path: { type: 'string', title: 'Путь внутри проекта', placeholder: 'build.gradle.kts' } } }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'sast', label: 'SAST Scanner', description: 'Анализ Kotlin-кода', category: 'Безопасность', params_schema: { type: 'object', properties: { path: { type: 'string', title: 'Путь внутри проекта', placeholder: 'src/main' } } }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'condition', label: 'IF / ELSE', description: 'Условное ветвление', category: 'Логика', params_schema: { type: 'object', properties: { expression: { type: 'string', title: 'Условие', placeholder: 'critical_count > 0' } }, required: ['expression'] }, inputs: [{id:'in'}], outputs: [{id:'true'}, {id:'false'}] },
  { kind: 'merge', label: 'Merge', description: 'Объединение результатов', category: 'Логика', params_schema: { type: 'object', properties: {} }, inputs: [{id:'left'}, {id:'right'}], outputs: [{id:'out'}] },
  { kind: 'compare', label: 'Compare', description: 'Сравнение значений', category: 'Логика', params_schema: { type: 'object', properties: { field: { type: 'string', title: 'Поле' }, operator: { type: 'string', title: 'Оператор', enum: ['eq','ne','gt','lt','gte','lte'] }, value: { type: 'string', title: 'Значение' } } }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'regex_filter', label: 'Regex Filter', description: 'Фильтрация по шаблону', category: 'Логика', params_schema: { type: 'object', properties: { pattern: { type: 'string', title: 'Регулярное выражение' }, field: { type: 'string', title: 'Поле' } }, required: ['pattern'] }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'ai', label: 'AI Analysis', description: 'Объяснение находок', category: 'AI', params_schema: { type: 'object', properties: { prompt: { type: 'string', title: 'Инструкция модели' } } }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'report', label: 'Report', description: 'Формирование отчёта', category: 'Отчёты', params_schema: { type: 'object', properties: { format: { type: 'string', title: 'Формат', enum: ['json','markdown'] } } }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
  { kind: 'telegram', label: 'Telegram', description: 'Уведомление', category: 'Уведомления', params_schema: { type: 'object', properties: { chatId: { type: 'string', title: 'Chat ID' }, credential_id: { type: 'string', title: 'ID credential (без токена)' } } }, inputs: [{id:'in'}], outputs: [] },
  { kind: 'ci_gate', label: 'CI Gate', description: 'Блокировка CI при находках', category: 'Интеграции', params_schema: { type: 'object', properties: { fail_on: { type: 'string', title: 'Порог критичности', enum: ['critical','high','medium','low'] } } }, inputs: [{id:'in'}], outputs: [{id:'out'}] },
];

export async function fetchNodeTypes(signal?: AbortSignal): Promise<NodeTypeDefinition[]> {
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '';
  try {
    const response = await fetch(`${base}/node-types`, { signal });
    if (!response.ok) throw new Error(`GET /node-types: ${response.status}`);
    const payload: unknown = await response.json();
    const items = Array.isArray(payload) ? payload : (payload && typeof payload === 'object' && 'node_types' in payload ? (payload as {node_types:unknown}).node_types : null);
    if (!Array.isArray(items) || !items.every(x => x && typeof x.kind === 'string' && typeof x.label === 'string' && x.params_schema && typeof x.params_schema === 'object')) throw new Error('Некорректный ответ /node-types');
    return items as NodeTypeDefinition[];
  } catch (error) {
    if (signal?.aborted) throw error;
    console.warn('Node types API unavailable, using local mock:', error);
    return mockNodeTypes;
  }
}
