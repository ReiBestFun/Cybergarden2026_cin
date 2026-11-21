import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { editTemplate, instantiateTemplate, listTemplates, removeTemplate, type WorkflowTemplate } from '../shared/api/account';
import { errorMessage } from '../shared/api/client';
import { listProjects, type Project } from '../shared/api/workflows';

export function Templates() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<WorkflowTemplate[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [editing, setEditing] = useState<WorkflowTemplate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    Promise.all([listTemplates(), listProjects()]).then(([stored, repositories]) => {
      if (!active) return;
      setTemplates(stored.items);
      setProjects(repositories.items);
      setSelectedProject(repositories.items[0]?.id ?? '');
    }, failure => { if (active) setError(errorMessage(failure)); });
    return () => { active = false; };
  }, []);

  async function change(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try { await action(); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }

  function save(event: FormEvent<HTMLFormElement>, template: WorkflowTemplate) {
    event.preventDefault();
    void change(async () => {
      const updated = await editTemplate(template.id, { name: template.name, description: template.description });
      setTemplates(items => items?.map(item => item.id === updated.id ? updated : item) ?? null);
      setEditing(null);
    });
  }

  return (
    <div className="page">
      <div className="page-heading">
        <h1>Мои шаблоны</h1>
        <Link className="btn secondary" to="/editor">Открыть редактор</Link>
      </div>
      {error && <p className="notice error" role="alert">{error}</p>}
      {templates === null && !error && <p className="muted">Загрузка шаблонов…</p>}
      {templates?.length === 0 && <p className="panel">В редакторе нажмите «Сохранить как шаблон», чтобы переиспользовать граф.</p>}
      {!!templates?.length && (
        <div className="panel account-form">
          {projects.length ? (
            <label>Репозиторий для нового сценария
              <select value={selectedProject} onChange={event => setSelectedProject(event.target.value)}>
                {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </label>
          ) : <Link to="/">Добавить репозиторий для шаблона</Link>}
        </div>
      )}
      {templates?.map(template => (
        <section className="panel" key={template.id}>
          {editing?.id === template.id ? (
            <form className="account-form" onSubmit={event => save(event, editing)}>
              <label>Название<input value={editing.name} required maxLength={120}
                onChange={event => setEditing({ ...editing, name: event.target.value })} /></label>
              <label>Описание<textarea value={editing.description} maxLength={1000}
                onChange={event => setEditing({ ...editing, description: event.target.value })} /></label>
              <div className="toolbar-actions">
                <button className="btn primary" disabled={busy}>Сохранить</button>
                <button className="btn secondary" type="button" onClick={() => setEditing(null)}>Отмена</button>
              </div>
            </form>
          ) : (
            <div className="account-row">
              <div>
                <h3>{template.name}</h3>
                {template.description && <p className="muted">{template.description}</p>}
                <p className="muted">{template.graph.nodes.length} нод</p>
              </div>
              <div className="toolbar-actions">
                <button className="btn secondary" disabled={busy} onClick={() => setEditing(template)}>Изменить</button>
                <button className="btn secondary" disabled={busy} onClick={() => void change(async () => {
                  await removeTemplate(template.id);
                  setTemplates(items => items?.filter(item => item.id !== template.id) ?? null);
                })}>Удалить</button>
                <button className="btn primary" disabled={busy || !selectedProject} onClick={() => void change(async () => {
                  const workflow = await instantiateTemplate(template.id, selectedProject);
                  navigate(`/editor?workflow=${workflow.id}`);
                })}>Создать сценарий</button>
              </div>
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
