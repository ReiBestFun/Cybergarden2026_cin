import { useEffect, useState, type FormEvent } from 'react';
import { useWorkspace } from '../app/workspaceContext';
import { errorMessage } from '../shared/api/client';
import { createMcpToken, listMcpTokens, revokeMcpToken, type IssuedMcpToken, type McpToken } from '../shared/api/mcpAccess';

export function McpAccessSettings() {
  const { workspace, withWorkspaceLock } = useWorkspace();
  const [items, setItems] = useState<McpToken[]>();
  const [name, setName] = useState('');
  const [issued, setIssued] = useState<IssuedMcpToken>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void listMcpTokens(workspace.id, controller.signal).then(tokens => {
      if (!controller.signal.aborted) setItems(tokens);
    }).catch(failure => {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    });
    return () => controller.abort();
  }, [workspace.id, attempt]);

  async function change(action: () => Promise<void>) {
    if (pending) return;
    setPending(true);
    setStatus('');
    setError('');
    try {
      await withWorkspaceLock(action);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setPending(false);
    }
  }

  function issue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;
    void change(async () => {
      const token = await createMcpToken(workspace.id, name.trim());
      const { token: _secret, ...metadata } = token;
      setItems(current => [metadata, ...(current ?? [])]);
      setIssued(token);
      setName('');
    });
  }

  async function copy(token: string) {
    try {
      await navigator.clipboard.writeText(token);
      setStatus('Токен скопирован.');
    } catch {
      setStatus('Выделите и скопируйте токен из поля.');
    }
  }

  return <section className="panel account-form" aria-label="Подключения MCP">
    <h2>Подключения MCP</h2>
    <p className="muted">Токены читают запуски и находки пространства «{workspace.name}». Срок доступа — 7 дней.</p>
    {items && <form className="account-form" onSubmit={issue}>
      <label>Название подключения<input value={name} required maxLength={120} onChange={event => setName(event.target.value)} /></label>
      <div className="toolbar-actions"><button className="btn primary" disabled={pending || !name.trim()}>Выпустить токен MCP</button></div>
    </form>}
    {issued && <div className="account-form">
      <label>Токен MCP
        <input type="password" value={issued.token} readOnly onFocus={event => event.target.select()} />
      </label>
      <p className="muted">Скопируйте токен в `CYBERFLOW_MCP_TOKEN` в настройках клиента. Значение показывается один раз.</p>
      <div className="toolbar-actions">
        <button className="btn secondary" onClick={() => void copy(issued.token)}>Скопировать токен</button>
        <button className="btn secondary" onClick={() => { setIssued(undefined); setStatus(''); }}>Скрыть</button>
      </div>
    </div>}
    {status && <p role="status">{status}</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {!items && error && <button className="btn secondary" onClick={() => setAttempt(value => value + 1)}>Повторить</button>}
    {!items && !error && <p role="status">Загружаем подключения…</p>}
    {items?.length === 0 && <p className="muted">Активных подключений нет.</p>}
    {items?.map(item => <div className="team-row" key={item.id}>
      <span>{item.name} · до {new Date(item.expires_at).toLocaleString('ru-RU')}</span>
      <button className="btn secondary" disabled={pending} onClick={() => void change(async () => {
        await revokeMcpToken(workspace.id, item.id);
        if (issued?.id === item.id) setIssued(undefined);
        setItems(current => current?.filter(token => token.id !== item.id));
        setStatus('Доступ MCP отозван.');
      })}>Отозвать</button>
    </div>)}
  </section>;
}
