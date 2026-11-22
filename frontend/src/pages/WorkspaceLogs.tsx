import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, ArrowRight, Download, ListChecks, Settings2 } from 'lucide-react';
import { useWorkspace } from '../app/workspaceContext';
import { api, apiFile, errorMessage } from '../shared/api/client';

type Policy = {
  version: number; destination: string; audit_days: number; diagnostic_days: number;
  member_view: boolean; member_export: boolean; member_manage: boolean;
};
type Settings = { policy: Policy; permissions: { view: boolean; export: boolean; manage: boolean; grant: boolean }; destinations: string[] };
type Row = { id: string; at: string; action: string; result: string; actor_id: string | null; run_id: string | null };
type Entries = { items: Row[]; total: number };
const pageSize = 25;
const actions: Record<string, string> = {
  'invitation.issue': 'Создание приглашения', 'invitation.revoke': 'Отзыв приглашения', 'invitation.redeem': 'Вступление в команду',
  'member.role': 'Изменение роли', 'member.remove': 'Удаление участника', 'template.publish': 'Публикация шаблона',
  'credential.create': 'Добавление реквизитов', 'credential.update': 'Изменение реквизитов', 'delivery.configure': 'Настройка доставки',
  'llm.configure': 'Настройка модели', 'mcp.issue': 'Выпуск токена MCP', 'mcp.revoke': 'Отзыв токена MCP',
  'logs.policy': 'Изменение политики журналов', 'logs.export': 'Экспорт журнала', 'run.enqueue': 'Добавление запуска в очередь',
  'run.claim': 'Начало запуска', 'run.finish': 'Завершение запуска', 'node.start': 'Начало узла', 'node.finish': 'Завершение узла',
  'delivery.enqueue': 'Постановка письма или сообщения в очередь', 'delivery.claim': 'Отправка уведомления',
  'delivery.recover': 'Восстановление доставки', 'delivery.finish': 'Доставка уведомления',
};
const results: Record<string, string> = {
  succeeded: 'Успешно', denied: 'Отказано', failed: 'Ошибка', running: 'Выполняется', queued: 'В очереди',
  partial: 'Частично', skipped: 'Пропущено', accepted: 'Принято', unknown: 'Неизвестно',
  unchanged: 'Без изменений', cancelled: 'Отменено', sending: 'Отправляется',
};
const journals = [
  { value: 'audit', label: 'Аудит действий', icon: ListChecks },
  { value: 'diagnostic', label: 'Диагностика запусков', icon: Activity },
] as const;
const memberRights = { member_view: 'Просматривать', member_export: 'Экспортировать', member_manage: 'Менять политику' } as const;

export function WorkspaceLogs() {
  const { workspace, withWorkspaceLock } = useWorkspace();
  const base = '/api/workspaces/' + workspace.id + '/logs';
  const [settings, setSettings] = useState<Settings>();
  const [draft, setDraft] = useState<Policy>();
  const [kind, setKind] = useState<(typeof journals)[number]['value']>('audit');
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [entries, setEntries] = useState<Entries>();
  const [entriesError, setEntriesError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [actors, setActors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (workspace.personal || workspace.role !== 'owner') return;
    const controller = new AbortController();
    api<{ id: string; github_login: string }[]>('/api/workspaces/' + workspace.id + '/members', { signal: controller.signal })
      .then(members => { if (!controller.signal.aborted) setActors(Object.fromEntries(members.map(m => [m.id, m.github_login]))); })
      .catch(() => { /* Audit rows keep their stable actor ID if a login is unavailable. */ });
    return () => controller.abort();
  }, [workspace.id, workspace.personal, workspace.role]);

  useEffect(() => {
    const controller = new AbortController();
    api<Settings>(base + '/policy', { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setSettings(value); setDraft(value.policy); }
    }).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [base]);

  useEffect(() => {
    setEntries(undefined);
    setEntriesError('');
    if (!settings?.permissions.view) return;
    const controller = new AbortController();
    api<Entries>(base + '?' + new URLSearchParams({ kind, limit: String(pageSize), offset: String(offset) }), { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setEntries(value); })
      .catch(e => { if (!controller.signal.aborted) setEntriesError(errorMessage(e)); });
    return () => controller.abort();
  }, [base, settings, kind, offset, version]);

  async function change(operation: () => Promise<void>) {
    setBusy(true); setError(''); setSaved(false);
    try { await withWorkspaceLock(operation); setVersion(n => n + 1); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  function update(patch: Partial<Policy>) {
    setDraft(current => current && { ...current, ...patch }); setSaved(false);
  }

  return <main className="page team-settings workspace-logs"><header><h1>Журналы</h1><span>{workspace.name}</span></header>
    {error && <p className="notice error" role="alert">{error}</p>}
    {settings && draft ? <>
      {settings.permissions.view || settings.permissions.export ? <section className="panel"><h2>Записи журнала</h2>
        <div className="log-toolbar">
          <div className="segments log-kinds" role="radiogroup" aria-label="Вид журнала">
            {journals.map(({ value, label, icon: Icon }) => <label className="segment" key={value}>
              <input type="radio" name="journal" value={value} checked={kind === value} onChange={() => {
                setEntries(undefined); setEntriesError(''); setKind(value); setOffset(0);
              }} />
              <Icon size={16} aria-hidden="true" />{label}
            </label>)}
          </div>
          {settings.permissions.export && <button className="btn secondary" disabled={busy} onClick={() => void change(async () => {
            const blob = await apiFile(base + '/export?kind=' + kind);
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url; link.download = kind + '-' + workspace.id + '.json'; link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          })}><Download size={16} aria-hidden="true" />Скачать JSON</button>}
        </div>
        {entriesError && <p role="alert" className="notice error">{entriesError}</p>}
        {settings.permissions.view && (entries ? <>
          {entries.items.length === 0 ? <p className="log-empty">Записей пока нет.</p> : <ul className="log-records">{entries.items.map(row =>
            <li key={row.id}>
              <div className="log-entry"><strong>{actions[row.action] ?? row.action}</strong>
                <div className="log-meta"><time dateTime={row.at}>{new Date(row.at).toLocaleString('ru-RU')}</time>
                  {row.actor_id && <span title={row.actor_id}>Участник {actors[row.actor_id] ? '@' + actors[row.actor_id] : row.actor_id}</span>}
                  {row.run_id && <Link to={'/results?run=' + row.run_id}>Открыть запуск <ArrowRight size={13} aria-hidden="true" /></Link>}
                </div>
              </div>
              <span className="log-result" data-result={row.result}>{results[row.result] ?? row.result}</span>
            </li>)}</ul>}
          {entries.total > pageSize && <div className="toolbar-actions">
            <button className="btn secondary" disabled={offset === 0} onClick={() => setOffset(n => n - pageSize)}>Назад</button>
            <span>{offset + 1}–{Math.min(offset + pageSize, entries.total)} из {entries.total}</span>
            <button className="btn secondary" disabled={offset + pageSize >= entries.total} onClick={() => setOffset(n => n + pageSize)}>Далее</button>
          </div>}
        </> : !entriesError && <p role="status">Загружаем записи…</p>)}
      </section> : <p>Чтобы просматривать журнал, запросите доступ у владельца пространства.</p>}
      {settings.permissions.manage && <details className="panel log-policy" open={(!settings.permissions.view && !settings.permissions.export) || undefined}>
        <summary><Settings2 size={17} aria-hidden="true" />Хранение и доступ</summary>
        <form className="account-form" onSubmit={event => {
          event.preventDefault();
          void change(async () => {
            const reply = await api<Settings>(base + '/policy', { method: 'PUT', json: draft });
            setSettings(reply); setDraft(reply.policy); setSaved(true);
          });
        }}>
          <div className="account-fields">
            <label>Хранилище<select value={draft.destination} onChange={e => update({ destination: e.target.value })}>
              {settings.destinations.map(id => <option key={id} value={id}>{id === 'postgresql' ? 'PostgreSQL' : id}</option>)}
            </select></label>
            <label>Срок аудита, дней<input type="number" required min={1} max={3650} value={draft.audit_days || ''}
              onChange={e => update({ audit_days: Number(e.target.value) })} /></label>
            <label>Срок диагностики, дней<input type="number" required min={1} max={3650} value={draft.diagnostic_days || ''}
              onChange={e => update({ diagnostic_days: Number(e.target.value) })} /></label>
          </div>
          {!workspace.personal && settings.permissions.grant && <fieldset className="log-rights"><legend>Права участников</legend>
            {(Object.keys(memberRights) as (keyof typeof memberRights)[]).map(key =>
              <label key={key}><input type="checkbox" checked={draft[key]} onChange={e => update({ [key]: e.target.checked })} />{memberRights[key]}</label>)}
          </fieldset>}
          <div className="toolbar-actions"><button className="btn primary" disabled={busy}>Сохранить политику</button>
            {saved && <span role="status">Сохранено</span>}</div>
        </form>
      </details>}
    </> : !error && <p role="status">Загружаем настройки…</p>}
  </main>;
}
