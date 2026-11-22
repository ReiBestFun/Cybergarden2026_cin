import { useEffect, useState, type FormEvent } from 'react';
import { TelegramRecipients } from '../shared/components/TelegramRecipients';
import { useWorkspace } from '../app/workspaceContext';
import { ApiError, errorMessage } from '../shared/api/client';
import {
  getWorkspaceDelivery, updateWorkspaceDelivery, type ChannelSettings, type DeliveryChannel,
} from '../shared/api/workspaceDelivery';

function ChannelForm({ channel, initial }: { channel: DeliveryChannel; initial: ChannelSettings | null }) {
  const { workspace, withWorkspaceLock } = useWorkspace();
  const email = channel === 'email';
  const slack = channel === 'slack';
  const channelName = { email: 'SMTP', telegram: 'Telegram', slack: 'Slack' }[channel];
  const [recipients, setRecipients] = useState(initial?.recipients.join('\n') ?? '');
  const [savedRecipients, setSavedRecipients] = useState(initial?.recipients ?? []);
  const [hasCredentials, setHasCredentials] = useState(initial?.has_credentials ?? false);
  const [version, setVersion] = useState(initial?.version ?? 0);
  const [mode, setMode] = useState(slack && !initial?.has_credentials ? 'replace' : 'keep');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const credentials = mode === 'replace'
      ? { secret: String(fields.get('secret')), ...(email ? { username: String(fields.get('username')) } : {}) }
      : null;
    setBusy(true);
    setSaved(false);
    setError('');
    setConflict(false);
    try {
      const updated = await withWorkspaceLock(() => updateWorkspaceDelivery(workspace.id, channel, {
        version,
        recipients: slack ? [] : recipients.split('\n').map(value => value.trim()).filter(Boolean),
        ...(mode === 'keep' ? {} : { credentials }),
      }));
      setRecipients(updated.recipients.join('\n'));
      setSavedRecipients(updated.recipients);
      setHasCredentials(updated.has_credentials);
      setVersion(updated.version);
      form.reset();
      setMode('keep');
      setSaved(true);
    } catch (failure) {
      setError(errorMessage(failure));
      setConflict(failure instanceof ApiError && failure.body.code === 'DELIVERY_SETTINGS_CHANGED');
    } finally {
      setBusy(false);
    }
  }

  return <form className="account-form" onSubmit={event => void submit(event)}>
    <h3>{channelName}</h3>
    {email ? <label>Почтовые адреса
      <textarea value={recipients} onChange={event => setRecipients(event.target.value)} rows={3} disabled={busy}
        placeholder="team@example.org" />
    </label> : slack ? <p className="muted">Сообщение отправляется в канал, к которому привязан Slack Incoming Webhook.</p>
      : <TelegramRecipients value={recipients} onChange={setRecipients} saved={savedRecipients} disabled={busy} />}
    <label>Реквизиты {channelName}
      <select value={mode} onChange={event => setMode(event.target.value)} disabled={busy}>
        <option value="keep">{hasCredentials ? 'Оставить сохранённые' : slack ? 'Канал отключён' : 'Оставить серверные'}</option>
        <option value="replace">{slack && !hasCredentials ? 'Подключить webhook' : 'Заменить'}</option>
        {hasCredentials && <option value="reset">{slack ? 'Отключить Slack' : 'Использовать серверные'}</option>}
      </select>
    </label>
    {mode === 'replace' && <>
      {email && <label>Логин SMTP<input name="username" required maxLength={320} autoComplete="off" disabled={busy} /></label>}
      <label>{email ? 'Пароль SMTP' : slack ? 'URL Slack Incoming Webhook' : 'Токен бота'}
        <input name="secret" type="password" required maxLength={email ? 16384 : slack ? 300 : 149}
          autoComplete="new-password" disabled={busy} />
      </label>
    </>}
    <div className="toolbar-actions"><button className="btn primary" disabled={busy || (slack && mode === 'keep')}>
      {busy ? 'Сохранение…' : `Сохранить ${channelName}`}
    </button></div>
    {saved && <p className="notice success" role="status">Настройки сохранены.</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {conflict && <button type="button" className="btn secondary" onClick={() => window.location.reload()}>Обновить страницу</button>}
  </form>;
}

export function WorkspaceDeliverySettings() {
  const { workspace } = useWorkspace();
  const [value, setValue] = useState<Awaited<ReturnType<typeof getWorkspaceDelivery>>>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void getWorkspaceDelivery(workspace.id, controller.signal).then(settings => {
      if (!controller.signal.aborted) setValue(settings);
    }).catch(failure => {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    });
    return () => controller.abort();
  }, [workspace.id, attempt]);

  return <section className="panel">
    <h2>Доставка отчётов</h2>
    <p className="muted">Для SMTP и Telegram пустой список получателей отключает командную доставку по каналу.</p>
    {error && <>
      <p className="notice error" role="alert">{error}</p>
      <button className="btn secondary" onClick={() => setAttempt(value => value + 1)}>Повторить</button>
    </>}
    {!value && !error && <p role="status">Загружаем настройки доставки…</p>}
    {value && <div className="workspace-delivery-settings">
      <ChannelForm channel="email" initial={value.email} />
      <ChannelForm channel="telegram" initial={value.telegram} />
      <ChannelForm channel="slack" initial={value.slack} />
    </div>}
  </section>;
}
