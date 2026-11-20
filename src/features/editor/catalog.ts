import type { NodeKind } from './types';
export const catalog: { kind: NodeKind; label: string; description: string; category: string }[] = [
  { kind: 'manual', label: 'Ручной запуск', description: 'Запуск по кнопке', category: 'Запуск' },
  { kind: 'schedule', label: 'Расписание', description: 'Периодический запуск', category: 'Запуск' },
  { kind: 'secrets', label: 'Secrets Scanner', description: 'Поиск секретов', category: 'Безопасность' },
  { kind: 'sca', label: 'SCA Scanner', description: 'Проверка зависимостей', category: 'Безопасность' },
  { kind: 'sast', label: 'SAST Scanner', description: 'Анализ Kotlin-кода', category: 'Безопасность' },
  { kind: 'condition', label: 'IF / ELSE', description: 'Условное ветвление', category: 'Логика' },
  { kind: 'ai', label: 'AI Analysis', description: 'Объяснение находок', category: 'AI' },
  { kind: 'telegram', label: 'Telegram', description: 'Уведомление', category: 'Уведомления' },
];

