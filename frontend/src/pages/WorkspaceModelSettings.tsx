import { useEffect, useState, type FormEvent } from 'react';
import { useWorkspace } from '../app/workspaceContext';
import { api, errorMessage } from '../shared/api/client';

const providerNames = { ollama: 'Ollama', openai: 'OpenAI', gemini: 'Google Gemini', groq: 'Groq' };
type Provider = keyof typeof providerNames;
type Profile = { provider: Provider; model: string };
type Catalog = Record<Provider, string[]>;

export function WorkspaceModelSettings() {
  const { workspace, withWorkspaceLock } = useWorkspace();
  const path = '/api/workspaces/' + workspace.id + '/llm';
  const [current, setCurrent] = useState<Profile>();
  const [catalog, setCatalog] = useState<Catalog>();
  const [provider, setProvider] = useState<Provider>('ollama');
  const [model, setModel] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const init = { signal: controller.signal };
    Promise.all([api<Profile>(path, init), api<Catalog>(path + '/models', init)])
      .then(([profile, models]) => {
        if (controller.signal.aborted) return;
        setCatalog(models);
        setCurrent(profile);
        setProvider(profile.provider);
        setModel(profile.model);
      }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [path]);

  function chooseProvider(next: Provider) {
    setProvider(next);
    setModel(next === current?.provider ? current.model : catalog?.[next][0] ?? '');
    setError('');
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const keyField = form.elements.namedItem('api_key') as HTMLInputElement | null;
    const profile = { provider, model, ...(keyField?.value ? { api_key: keyField.value } : {}) };
    setBusy(true);
    setError('');
    try {
      const saved = await withWorkspaceLock(() => api<Profile>(path, { method: 'PUT', json: profile }));
      if (keyField) keyField.value = '';
      setCurrent(saved);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel"><h2>Модель для отчётов и сценариев</h2>
    {error && <p className="notice error" role="alert">{error}</p>}
    {current && catalog ? <form className="account-form" onSubmit={save}>
      <p role="status">Активная модель — {providerNames[current.provider]} · {current.model}</p>
      <label>Провайдер<select value={provider} disabled={busy} onChange={e => chooseProvider(e.target.value as Provider)}>
        {Object.entries(providerNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select></label>
      <label>Модель<select name="model" value={model} required disabled={busy} onChange={e => setModel(e.target.value)}>
        {catalog[provider].map(id => <option key={id} value={id}>{id}</option>)}
      </select></label>
      {provider !== 'ollama' && <>
        <p>Для пояснения находок {providerNames[provider]} получит номера групп, идентификаторы правил, названия, критичность и количество совпадений.
          Для генерации сценария отправляется весь введённый текст, включая указанные в нём адреса и получателей.</p>
        <label>API-ключ {providerNames[provider]}<input key={provider} type="password" name="api_key" autoComplete="new-password"
          placeholder={provider === current.provider ? 'Оставьте пустым, чтобы использовать сохранённый ключ' : undefined}
          required={provider !== current.provider} maxLength={4096} disabled={busy} /></label>
      </>}
      {provider === 'ollama' && current.provider !== 'ollama' && <p>Переход на Ollama удалит сохранённый ключ {providerNames[current.provider]} этой команды.</p>}
      <button className="btn primary" disabled={busy || !model || (provider === 'ollama' && current.provider === provider && current.model === model)}>
        {busy ? 'Сохраняем…' : provider === 'ollama' ? 'Использовать Ollama' : 'Проверить и включить ' + providerNames[provider]}
      </button>
    </form> : !error && <p role="status">Загружаем настройки…</p>}
  </section>;
}
