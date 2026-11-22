import { useEffect, useState, type FormEvent } from 'react';
import { useWorkspace } from '../app/workspaceContext';
import { api, errorMessage } from '../shared/api/client';

type Profile = { provider: 'ollama' | 'openai'; model: string };

export function WorkspaceModelSettings() {
  const { workspace, withWorkspaceLock } = useWorkspace();
  const path = '/api/workspaces/' + workspace.id + '/llm';
  const [current, setCurrent] = useState<Profile>();
  const [provider, setProvider] = useState<Profile['provider']>('ollama');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api<Profile>(path, { signal: controller.signal }).then(profile => {
      if (!controller.signal.aborted) { setCurrent(profile); setProvider(profile.provider); }
    }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [path]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const profile = provider === 'ollama' ? { provider } : {
      provider, model: fields.get('model'), api_key: fields.get('api_key'),
    };
    setBusy(true);
    setError('');
    try {
      const saved = await withWorkspaceLock(() => api<Profile>(path, { method: 'PUT', json: profile }));
      form.reset();
      setCurrent(saved);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel"><h2>Модель для отчётов и сценариев</h2>
    {error && <p className="notice error" role="alert">{error}</p>}
    {current ? <form className="account-form" onSubmit={save}>
      <p role="status">Активная модель — {current.provider === 'ollama' ? 'Ollama' : 'OpenAI'} · {current.model}</p>
      <label>Провайдер<select value={provider} disabled={busy} onChange={e => setProvider(e.target.value as Profile['provider'])}>
        <option value="ollama">Ollama</option><option value="openai">OpenAI</option>
      </select></label>
      {provider === 'openai' && <>
        <p>Для пояснения находок OpenAI получит номера групп, идентификаторы правил, названия, критичность и количество совпадений.
          Для генерации сценария отправляется весь введённый текст, включая указанные в нём адреса и получателей.</p>
        <label>Модель OpenAI<input name="model" defaultValue={current.provider === 'openai' ? current.model : 'gpt-4o-mini'} required maxLength={128} disabled={busy} /></label>
        <label>API-ключ OpenAI<input type="password" name="api_key" autoComplete="new-password" required maxLength={4096} disabled={busy} /></label>
      </>}
      {provider === 'ollama' && current.provider === 'openai' && <p>Переход на Ollama удалит сохранённый ключ OpenAI этой команды.</p>}
      <button className="btn primary" disabled={busy || (provider === 'ollama' && current.provider === 'ollama')}>
        {busy ? 'Сохраняем…' : provider === 'openai' ? 'Проверить ключ и включить OpenAI' : 'Использовать Ollama'}
      </button>
    </form> : !error && <p role="status">Загружаем настройки…</p>}
  </section>;
}
