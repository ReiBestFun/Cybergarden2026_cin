import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { errorMessage } from '../shared/api/client';
import { copyTemplate, listTemplates, type WorkflowTemplate } from '../shared/api/templates';
import { ensureProject, listProjects, type Page, type Project } from '../shared/api/workflows';

const PAGE_SIZE = 12;

export function Templates() {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<WorkflowTemplate> | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [copyError, setCopyError] = useState('');
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const types = useNodeTypesStore(state => state.types);

  useEffect(() => {
    const controller = new AbortController();
    setPage(null);
    setCatalogError('');
    void listTemplates(offset, PAGE_SIZE, controller.signal).then(result => {
      if (!controller.signal.aborted) setPage(result);
    }).catch(error => {
      if (!controller.signal.aborted) setCatalogError(errorMessage(error));
    });
    return () => controller.abort();
  }, [offset]);

  useEffect(() => {
    let active = true;
    void listProjects().then(result => {
      if (!active) return;
      setProjects(result.items);
      setProjectId(result.items[0]?.id ?? '');
    }).catch(error => { if (active) setCopyError(errorMessage(error)); });
    return () => { active = false; };
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>, template: WorkflowTemplate) => {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(event.currentTarget);
    setCopyError('');
    setBusy(true);
    try {
      const target = projectId || (await ensureProject(String(fields.get('repository')))).id;
      const workflow = await copyTemplate(template.id, target, String(fields.get('name')));
      nav(`/editor?workflow=${workflow.id}`);
    } catch (error) {
      setCopyError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="page-heading"><h1>Шаблоны сценариев</h1><Link className="btn secondary" to="/editor">Открыть редактор</Link></div>
      {catalogError && <div className="notice error" role="alert">{catalogError}</div>}
      {!page && !catalogError && <p role="status">Загружаем шаблоны…</p>}
      {page?.total === 0 && <p className="muted">Опубликуй сценарий из редактора, чтобы он появился здесь.</p>}
      {page?.items.map(template => {
        const selected = selectedId === template.id;
        const names = new Map(template.graph.nodes.map(node => [node.id, types.find(type => type.kind === node.type)?.label ?? node.type]));
        return <article className="panel template-card" key={template.id}>
          <div className="page-heading">
            <div><h2>{template.name}</h2><p className="muted">{template.graph.nodes.length} нод · {new Date(template.created_at).toLocaleDateString('ru-RU')}</p></div>
            <button className="btn secondary" aria-expanded={selected} disabled={busy} onClick={() => {
              setSelectedId(selected ? '' : template.id);
              setCopyError('');
            }}>{selected ? 'Свернуть' : 'Просмотреть'}</button>
          </div>
          {template.description && <p className="template-description">{template.description}</p>}
          {selected && <>
            <ul className="template-connections">
              {template.graph.edges.map(edge => <li key={edge.id}>{names.get(edge.source)} <span className="muted">{edge.source_port} →</span> {names.get(edge.target)}</li>)}
            </ul>
            {template.schedule && <p className="muted">Параметры расписания будут скопированы. Включить расписание можно в редакторе.</p>}
            <form className="template-panel" onSubmit={event => void submit(event, template)}>
              <label>Название копии<input name="name" defaultValue={template.name} maxLength={120} required /></label>
              <label>Проект<select value={projectId} onChange={event => setProjectId(event.target.value)}>
                <option value="">Другой учебный репозиторий</option>
                {projects.map(project => <option key={project.id} value={project.id}>{project.name} · {project.repository_url}</option>)}
              </select></label>
              {!projectId && <label>URL репозитория<input name="repository" type="url" required placeholder="https://github.com/owner/repository" /></label>}
              {copyError && <p className="notice error" role="alert">{copyError}</p>}
              <button className="btn primary" disabled={busy}>{busy ? 'Копирование…' : 'Скопировать и открыть'}</button>
            </form>
          </>}
        </article>;
      })}
      {page && page.total > PAGE_SIZE && <div className="toolbar-actions">
        <button className="btn secondary" disabled={offset === 0 || busy} onClick={() => setOffset(offset - PAGE_SIZE)}>Назад</button>
        <button className="btn secondary" disabled={offset + PAGE_SIZE >= page.total || busy} onClick={() => setOffset(offset + PAGE_SIZE)}>Дальше</button>
      </div>}
    </div>
  );
}
