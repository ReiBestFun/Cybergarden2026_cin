import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { errorMessage } from '../shared/api/client';
import { createCredential, credentialKinds, listCredentials, updateCredential, type Credential, type CredentialKind } from '../shared/api/credentials';
import { listProjects, type Project } from '../shared/api/workflows';

function CredentialForm({ projectId, editing, onSaved, onCancel }: {
  projectId: string; editing: Credential | null; onSaved: () => void; onCancel: () => void;
}) {
  const [kind, setKind] = useState<CredentialKind>(editing?.kind ?? 'telegram');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError('');
    try {
      const privateKey = data.get('privateKey');
      if (privateKey instanceof File && privateKey.size > 16384) throw new Error('Выберите файл SSH-ключа размером до 16 КБ.');
      const secret = privateKey instanceof File ? await privateKey.text() : String(data.get('secret') ?? '');
      const username = String(data.get('username') ?? '').trim();
      const passphrase = String(data.get('passphrase') ?? '');
      const value = { secret, username: username || undefined, passphrase: passphrase || undefined };
      if (editing) await updateCredential(projectId, editing.id, { value });
      else await createCredential(projectId, { name: String(data.get('name')), kind, value });
      form.reset();
      onSaved();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel credential-form" onSubmit={submit}>
      <h2>{editing ? `Заменить секрет · ${editing.name}` : 'Добавить учётные данные'}</h2>
      {error && <div className="notice error" role="alert">{error}</div>}
      {!editing && <>
        <label>Название<input name="name" required maxLength={120} /></label>
        <label>Тип<select value={kind} onChange={event => setKind(event.target.value as CredentialKind)}>
          {Object.entries(credentialKinds).map(([id, item]) => <option key={id} value={id}>{item.label}</option>)}
        </select></label>
      </>}
      {!credentialKinds[kind].ready && <p className="muted">Секрет можно сохранить. Подключение к нодам появится вместе с интеграцией.</p>}
      {kind === 'smtp' && <label>Логин SMTP<input name="username" maxLength={320} autoComplete="off" placeholder="По умолчанию используется логин сервера" /></label>}
      {kind === 'ssh_key' ? <>
        <label>Файл приватного ключа<input name="privateKey" type="file" required /></label>
        <label>Пароль ключа<input name="passphrase" type="password" maxLength={1024} autoComplete="new-password" /></label>
      </> : <label key={kind}>Значение<input name="secret" type="password" required maxLength={16384} autoComplete="new-password" /></label>}
      <div className="toolbar-actions">
        <button className="btn primary" disabled={busy}>Сохранить</button>
        <button className="btn secondary" type="button" disabled={busy} onClick={onCancel}>Отмена</button>
      </div>
    </form>
  );
}

function ProjectCredentials({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<Credential[] | null>(null);
  const [editing, setEditing] = useState<Credential | null | undefined>();
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void listCredentials(projectId, controller.signal).then(setItems).catch(failure => {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    });
    return () => controller.abort();
  }, [projectId, revision]);

  const toggle = async (item: Credential) => {
    setBusy(true);
    setError('');
    try {
      const updated = await updateCredential(projectId, item.id, { active: !item.active });
      setItems(items => items?.map(entry => entry.id === item.id ? updated : entry) ?? null);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  return <>
    {error && <div className="notice error" role="alert">{error}</div>}
    {editing === undefined ? (
      <button className="btn primary" onClick={() => setEditing(null)}>Добавить учётные данные</button>
    ) : <CredentialForm key={editing?.id ?? 'new'} projectId={projectId} editing={editing} onCancel={() => setEditing(undefined)}
      onSaved={() => { setError(''); setEditing(undefined); setRevision(number => number + 1); }} />}
    {items?.map(item => (
      <div className="panel credential-row" key={item.id}>
        <div><strong>{item.name}</strong><p className="muted">{credentialKinds[item.kind].label} · {item.active ? 'Активен' : 'Отключён'}</p></div>
        <div className="toolbar-actions">
          <button className="btn secondary" onClick={() => setEditing(item)} disabled={editing !== undefined}>Заменить секрет</button>
          <button className="btn secondary" onClick={() => void toggle(item)} disabled={busy}>{item.active ? 'Отключить' : 'Включить'}</button>
        </div>
      </div>
    ))}
    {items?.length === 0 && <p className="muted">В этом проекте ещё нет учётных данных.</p>}
  </>;
}

export function Credentials() {
  const [params, setParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void listProjects().then(page => { if (active) setProjects(page.items); })
      .catch(failure => { if (active) setError(errorMessage(failure)); });
    return () => { active = false; };
  }, []);

  const selected = projects?.find(item => item.id === params.get('project')) ?? projects?.[0];
  return (
    <div className="page">
      <h1>Учётные данные</h1>
      {error && <div className="notice error" role="alert">{error}</div>}
      {projects?.length === 0 && <p>Сначала <Link to="/editor">сохрани сценарий</Link>, чтобы создать проект.</p>}
      {selected && <>
        <label className="credential-project">Проект<select value={selected.id} onChange={event => setParams({ project: event.target.value })}>
          {projects?.map(item => <option key={item.id} value={item.id}>{item.name} · {item.repository_url}</option>)}
        </select></label>
        <ProjectCredentials key={selected.id} projectId={selected.id} />
      </>}
    </div>
  );
}
