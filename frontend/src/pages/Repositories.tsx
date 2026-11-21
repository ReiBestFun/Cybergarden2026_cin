import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useEditorStore } from '../features/editor/store';
import { setLastRunId } from '../features/editor/defaults';
import { useWorkspace } from '../app/workspaceContext';
import { errorMessage } from '../shared/api/client';
import { createProject, listProjects, moveProject, updateProject, type Project } from '../shared/api/workflows';

export function Repositories() {
  const navigate = useNavigate();
  const { workspace, workspaces, withWorkspaceLock } = useWorkspace();
  const targets = workspaces.filter(item => !item.personal && item.role === 'owner');
  const newWorkflow = useEditorStore(state => state.newWorkflow);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [ref, setRef] = useState('main');
  const [editing, setEditing] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [moving, setMoving] = useState<Project | null>(null);
  const [targetId, setTargetId] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    listProjects(workspace.id, controller.signal).then(items => {
      if (!controller.signal.aborted) setProjects(items);
    }, failure => {
      if (!controller.signal.aborted) { setProjects([]); setError(errorMessage(failure)); }
    });
    return () => controller.abort();
  }, [workspace.id]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const fields = { name: name.trim(), default_ref: ref.trim() };
      const saved = editing
        ? await updateProject(editing.id, fields)
        : await createProject({ ...fields, repository_url: url.trim(), workspace_id: workspace.id });
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

  async function transfer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = targets.find(item => item.id === targetId);
    if (!moving || !target || busy) return;
    const project = moving;
    const editor = useEditorStore.getState();
    if (editor.projectId === project.id && editor.hasUnsavedChanges()
      && !window.confirm('Отбросить несохранённые изменения сценария этого проекта и продолжить перенос?')) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await withWorkspaceLock(async () => {
        await moveProject(project.id, target.id);
        const currentEditor = useEditorStore.getState();
        if (currentEditor.workspaceId === workspace.id) {
          if (currentEditor.projectId === project.id) currentEditor.newWorkflow();
          setLastRunId(null);
        }
        setProjects(items => items?.filter(item => item.id !== project.id) ?? []);
        if (editing?.id === project.id) resetForm();
        setMoving(null);
        setNotice(`Проект «${project.name}» перенесён в «${target.name}».`);
      });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-heading">
        <h1>Репозитории</h1>
        <Link to="/scenarios" className="btn secondary">Сценарии</Link>
      </div>
      {workspace.role === 'owner' && <form className="panel account-form" onSubmit={event => void save(event)}>
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
      </form>}
      {notice && <p className="notice success" role="status">{notice}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      {projects === null && !error && <p className="muted">Загрузка репозиториев…</p>}
      {projects?.length === 0 && !error && <p className="muted">В этом пространстве пока нет репозиториев.</p>}
      {projects?.map(project => (
        <section className="panel account-row" key={project.id}>
          <div>
            <h3>{project.name}</h3>
            <p className="muted">{project.repository_url} · <code>{project.default_ref}</code></p>
          </div>
          {workspace.role === 'owner' && <div className="toolbar-actions">
            <button className="btn secondary" onClick={() => edit(project)} disabled={busy}>Изменить</button>
            {workspace.personal && targets.length > 0 && <button className="btn secondary" disabled={busy}
              onClick={() => { setMoving(project); setTargetId(targets.length === 1 ? targets[0].id : ''); setError(''); setNotice(''); }}>
              Перенести в команду
            </button>}
            <button className="btn primary" onClick={() => { newWorkflow(undefined, project); navigate('/editor'); }}>
              Создать сценарий
            </button>
          </div>}
        </section>
      ))}
      {moving && <form className="panel account-form" onSubmit={event => void transfer(event)}>
        <h2>Перенести «{moving.name}»</h2>
        <p>Команда получит доступ к проекту, его сценариям и истории запусков. Настроенные интеграции сохранятся.</p>
        <label>Целевое пространство<select value={targetId} required onChange={event => setTargetId(event.target.value)}>
          <option value="">Выберите команду</option>
          {targets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <div className="toolbar-actions">
          <button className="btn primary" disabled={busy || !targetId}>Подтвердить перенос</button>
          <button className="btn secondary" type="button" onClick={() => setMoving(null)} disabled={busy}>Отмена</button>
        </div>
      </form>}
    </div>
  );
}
