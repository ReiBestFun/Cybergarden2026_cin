import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useEditorStore } from '../features/editor/store';
import { errorMessage } from '../shared/api/client';
import { createProject, listProjects, updateProject, type Project } from '../shared/api/workflows';

export function Repositories() {
  const navigate = useNavigate();
  const newWorkflow = useEditorStore(state => state.newWorkflow);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [ref, setRef] = useState('main');
  const [editing, setEditing] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    listProjects().then(page => {
      if (active) setProjects(page.items);
    }, failure => {
      if (active) { setProjects([]); setError(errorMessage(failure)); }
    });
    return () => { active = false; };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const fields = { name: name.trim(), default_ref: ref.trim() };
      const saved = editing
        ? await updateProject(editing.id, fields)
        : await createProject({ ...fields, repository_url: url.trim() });
      setProjects(items => {
        const others = items ?? [];
        return others.some(project => project.id === saved.id)
          ? others.map(project => project.id === saved.id ? saved : project)
          : [saved, ...others];
      });
      resetForm();
      setNotice('Репозиторий сохранён.');
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  function resetForm() {
    setEditing(null);
    setName('');
    setUrl('');
    setRef('main');
  }

  function edit(project: Project) {
    setEditing(project);
    setName(project.name);
    setUrl(project.repository_url);
    setRef(project.default_ref);
    setNotice('');
  }

  return (
    <div className="page">
      <div className="page-heading">
        <h1>Мои репозитории</h1>
        <Link to="/scenarios" className="btn secondary">Мои сценарии</Link>
      </div>
      <form className="panel account-form" onSubmit={event => void save(event)}>
        <h2>{editing ? 'Настройки репозитория' : 'Добавить репозиторий'}</h2>
        <div className="account-fields">
          <label>Название<input value={name} onChange={event => setName(event.target.value)} required maxLength={120} /></label>
          <label>URL GitHub<input value={url} onChange={event => setUrl(event.target.value)} type="url" required maxLength={2048}
            readOnly={!!editing} placeholder="https://github.com/owner/repository" /></label>
          <label>Ветка по умолчанию<input value={ref} onChange={event => setRef(event.target.value)} required maxLength={255} /></label>
        </div>
        <div className="toolbar-actions">
          <button className="btn primary" disabled={busy}>{editing ? 'Сохранить изменения' : 'Добавить'}</button>
          {editing && <button className="btn secondary" type="button" onClick={resetForm} disabled={busy}>Отмена</button>}
        </div>
      </form>
      {notice && <p className="notice success" role="status">{notice}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      {projects === null && !error && <p className="muted">Загрузка репозиториев…</p>}
      {projects?.length === 0 && !error && <p className="muted">В профиле пока нет репозиториев.</p>}
      {projects?.map(project => (
        <section className="panel account-row" key={project.id}>
          <div>
            <h3>{project.name}</h3>
            <p className="muted">{project.repository_url} · <code>{project.default_ref}</code></p>
          </div>
          <div className="toolbar-actions">
            <button className="btn secondary" onClick={() => edit(project)} disabled={busy}>Изменить</button>
            <button className="btn primary" onClick={() => { newWorkflow(undefined, project); navigate('/editor'); }}>
              Создать сценарий
            </button>
          </div>
        </section>
      ))}
    </div>
  );
}
