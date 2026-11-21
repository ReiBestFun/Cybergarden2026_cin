import { useEffect, useMemo, useState } from 'react';
import { CalendarClock, CircleAlert } from 'lucide-react';
import { useEditorStore } from './store';
import { browserTimeZone, hasScheduleFields, scheduleSettings, type ScheduleDraft } from './defaults';
import { previewSchedule, type Frequency } from '../../shared/api/workflows';
import { errorMessage } from '../../shared/api/client';

const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: 'daily', label: 'Ежедневно' },
  { value: 'weekly', label: 'Еженедельно' },
  { value: 'monthly', label: 'Ежемесячно' },
  { value: 'cron', label: 'Свой cron (продвинутый)' },
];
const DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const DAYS_FULL = ['понедельникам', 'вторникам', 'средам', 'четвергам', 'пятницам', 'субботам', 'воскресеньям'];
const CRON_PRESETS = [
  { cron: '0 9 * * 1-5', label: 'По будням в 9:00' },
  { cron: '0 3 * * *', label: 'Каждую ночь в 3:00' },
  { cron: '0 * * * *', label: 'Каждый час' },
  { cron: '*/15 * * * *', label: 'Каждые 15 минут' },
];
const POPULAR_ZONES = [
  'Europe/Moscow', 'Europe/Kaliningrad', 'Europe/Samara', 'Asia/Yekaterinburg', 'Asia/Omsk',
  'Asia/Novosibirsk', 'Asia/Krasnoyarsk', 'Asia/Irkutsk', 'Asia/Vladivostok', 'UTC',
];

function allTimeZones(): string[] {
  const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
  try { return supported ? supported('timeZone') : POPULAR_ZONES; } catch { return POPULAR_ZONES; }
}

function describe(s: ScheduleDraft): string {
  if (!hasScheduleFields(s)) return 'Заполните настройки расписания';
  if (s.frequency === 'daily') return `Каждый день в ${s.time}`;
  if (s.frequency === 'weekly') {
    const days = [...s.weekdays].sort();
    if (days.length === 7) return `Каждый день в ${s.time}`;
    if (days.join() === '0,1,2,3,4') return `По будням в ${s.time}`;
    if (days.join() === '5,6') return `По выходным в ${s.time}`;
    return days.length ? `По ${days.map(d => DAYS_FULL[d]).join(', ')} в ${s.time}` : 'Дни недели не выбраны';
  }
  if (s.frequency === 'monthly') {
    return `Каждый месяц ${s.day_of_month}-го числа в ${s.time}` + (s.day_of_month > 28 ? ' (в коротких месяцах — в последний день)' : '');
  }
  return `Cron: ${s.cron || '—'}`;
}

const dayKey = (date: Date, timeZone: string) => new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);

function formatSlot(iso: string, timeZone: string): string {
  const date = new Date(iso);
  const now = new Date();
  const time = date.toLocaleTimeString('ru-RU', { timeZone, hour: '2-digit', minute: '2-digit' });
  const tomorrow = new Date(now.getTime() + 86_400_000);
  if (dayKey(date, timeZone) === dayKey(now, timeZone)) return `сегодня в ${time}`;
  if (dayKey(date, timeZone) === dayKey(tomorrow, timeZone)) return `завтра в ${time}`;
  const day = date.toLocaleDateString('ru-RU', { timeZone, weekday: 'short', day: 'numeric', month: 'long' });
  return `${day} в ${time}`;
}

function relative(iso: string): string {
  const minutes = Math.max(1, Math.round((new Date(iso).getTime() - Date.now()) / 60_000));
  if (minutes < 60) return `через ${minutes} мин`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `через ${hours} ч`;
  return `через ${Math.round(hours / 24)} дн`;
}

export function ScheduleSettings() {
  const { schedule, setSchedule, serverSchedule, workflowId } = useEditorStore();
  const [runs, setRuns] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const zones = useMemo(() => {
    const all = allTimeZones();
    const popular = [...new Set([browserTimeZone(), ...POPULAR_ZONES])];
    const rest = all.filter(z => !popular.includes(z));
    if (!all.includes(schedule.timezone) && !popular.includes(schedule.timezone)) rest.unshift(schedule.timezone);
    return { popular, rest };
  }, [schedule.timezone]);

  // Live preview: the backend computes the next slots with the same code the scheduler uses.
  const settingsKey = hasScheduleFields(schedule) ? JSON.stringify(scheduleSettings(schedule)) : null;
  useEffect(() => {
    setRuns([]);
    setError(null);
    if (settingsKey === null) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const result = await previewSchedule(JSON.parse(settingsKey), controller.signal);
        if (controller.signal.aborted) return;
        setRuns(result.next_runs);
        setError(null);
      } catch (e) {
        if (controller.signal.aborted) return;
        setRuns([]);
        setError(errorMessage(e));
      }
    }, 350);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [settingsKey]);

  const toggleDay = (day: number) => setSchedule({
    weekdays: schedule.weekdays.includes(day) ? schedule.weekdays.filter(d => d !== day) : [...schedule.weekdays, day].sort(),
  });

  return (
    <div className="schedule-settings">
      <label>
        Периодичность
        <select value={schedule.frequency} onChange={e => setSchedule({ frequency: e.target.value as Frequency })}>
          {FREQUENCIES.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </label>

      {schedule.frequency === 'weekly' && (
        <div className="field">
          <span>Дни недели</span>
          <div className="weekday-picker" role="group" aria-label="Дни недели">
            {DAYS.map((label, day) => (
              <button key={day} type="button" className={schedule.weekdays.includes(day) ? 'active' : ''}
                aria-pressed={schedule.weekdays.includes(day)} onClick={() => toggleDay(day)}>{label}</button>
            ))}
          </div>
          <div className="chips">
            <button type="button" onClick={() => setSchedule({ weekdays: [0, 1, 2, 3, 4] })}>Будни</button>
            <button type="button" onClick={() => setSchedule({ weekdays: [5, 6] })}>Выходные</button>
          </div>
        </div>
      )}

      {schedule.frequency === 'monthly' && (
        <label>
          День месяца
          <select value={schedule.day_of_month} onChange={e => setSchedule({ day_of_month: Number(e.target.value) })}>
            {schedule.day_of_month === 0 && <option value={0} disabled>Выберите день</option>}
            {Array.from({ length: 31 }, (_, i) => i + 1).map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
      )}

      {schedule.frequency === 'cron' ? (
        <div className="field">
          <label>
            Cron-выражение
            <input className="mono" value={schedule.cron} placeholder="0 9 * * 1-5" spellCheck={false}
              onChange={e => setSchedule({ cron: e.target.value })} />
          </label>
          <small className="muted">минута · час · день месяца · месяц · день недели (0 или 7 — воскресенье)</small>
          <div className="chips">
            {CRON_PRESETS.map(p => <button key={p.cron} type="button" onClick={() => setSchedule({ cron: p.cron })}>{p.label}</button>)}
          </div>
        </div>
      ) : (
        <label>
          Время запуска
          <input type="time" value={schedule.time} onChange={e => setSchedule({ time: e.target.value })} />
        </label>
      )}

      <label>
        Часовой пояс
        <select value={schedule.timezone} onChange={e => setSchedule({ timezone: e.target.value })}>
          <optgroup label="Популярные">
            {zones.popular.map(z => <option key={z} value={z}>{z}</option>)}
          </optgroup>
          <optgroup label="Все">
            {zones.rest.map(z => <option key={z} value={z}>{z}</option>)}
          </optgroup>
        </select>
      </label>

      <label className="checkbox">
        <input type="checkbox" checked={schedule.enabled} onChange={e => setSchedule({ enabled: e.target.checked })} />
        Расписание включено
      </label>

      <div className={`schedule-preview${error ? ' invalid' : ''}`}>
        <strong><CalendarClock size={15} /> {describe(schedule)}</strong>
        {error ? (
          <span className="error-text"><CircleAlert size={14} /> {error}</span>
        ) : runs.length > 0 && (
          <>
            <span>{schedule.enabled ? 'Следующий запуск' : 'Следующий запуск (после включения)'}: {formatSlot(runs[0], schedule.timezone)} ({relative(runs[0])})</span>
            {runs.length > 1 && <small className="muted">Затем: {runs.slice(1, 4).map(r => formatSlot(r, schedule.timezone)).join('; ')}</small>}
          </>
        )}
      </div>

      {workflowId && serverSchedule && (
        <div className="schedule-server muted">
          На сервере: {serverSchedule.enabled ? 'включено' : 'выключено'}
          {serverSchedule.last_enqueued_at && <> · последний автозапуск {new Date(serverSchedule.last_enqueued_at).toLocaleString('ru-RU')}</>}
          {serverSchedule.last_error && <span className="error-text"><CircleAlert size={14} /> {serverSchedule.last_error.message}</span>}
        </div>
      )}
      <p className="muted">Изменения вступают в силу после «Сохранить». Запуски выполняет воркер на сервере, держать сайт открытым не нужно.</p>
    </div>
  );
}
