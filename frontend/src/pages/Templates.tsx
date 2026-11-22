import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { BookMarked, CalendarDays, ChevronDown, ChevronUp, LockKeyhole, UsersRound, Workflow } from 'lucide-react';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { errorMessage } from '../shared/api/client';
import { copyTemplate, editTemplate, listTemplates, publishDraft, removeTemplate, type WorkflowTemplate } from '../shared/api/templates';
import { ensureProject, listProjects, type Page, type Project } from '../shared/api/workflows';
import { useWorkspace } from '../app/workspaceContext';

const PAGE_SIZE = 12;

function GraphPreview({ graph }: Pick<WorkflowTemplate, 'graph'>) {
  if (!graph.nodes.length) return null;
  const points = graph.nodes.map(node => node.position);
  const left = Math.min(...points.map(p => p.x)) - 32;
  const top = Math.min(...points.map(p => p.y)) - 28;
  const width = Math.max(...points.map(p => p.x)) - left + 158;
  const height = Math.max(...points.map(p => p.y)) - top + 80;
  const positions = new Map(graph.nodes.map(node => [node.id, node.position]));
  return <div className="graph-preview" aria-hidden="true">
    <svg viewBox={[left, top, width, height].join(' ')}>
      {graph.edges.map(edge => {
        const from = positions.get(edge.source), to = positions.get(edge.target);
        if (!from || !to) return null;
        const x = from.x + 130, y = from.y + 26, end = to.y + 26, mid = (x + to.x) / 2;
        return <path key={edge.id} d={`M ${x} ${y} C ${mid} ${y}, ${mid} ${end}, ${to.x} ${end}`} />;
      })}
      {graph.nodes.map(node => <g key={node.id} transform={`translate(${node.position.x} ${node.position.y})`}>
        <rect width="130" height="52" rx="10" />
        <circle cx="22" cy="26" r="8" /><line x1="46" x2="108" y1="21" y2="21" /><line x1="46" x2="84" y1="32" y2="32" />
      </g>)}
    </svg>
  </div>;
}

export function Templates() {
  const { workspace, workspaces, withWorkspaceLock } = useWorkspace();
  const personal = workspaces.find(item => item.personal);
  const [copyToPersonal, setCopyToPersonal] = useState(false);
  const copyRequest = useRef<AbortController>();
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<WorkflowTemplate> | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const selectedTemplate = page?.items.find(item => item.id === selectedId);
  const legacyDraft = selectedTemplate?.can_edit && !selectedTemplate.draft_source_workspace_id;
  const canCopyToTeam = !workspace.personal && workspace.role === 'owner' && !legacyDraft;
  const destination = canCopyToTeam && !copyToPersonal ? workspace : personal;
  const destinationId = destination?.id;
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [copyError, setCopyError] = useState('');
  const [editing, setEditing] = useState<WorkflowTemplate | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const types = useNodeTypesStore(state => state.types);

  useEffect(() => {
    const controller = new AbortController();
    setPage(null);
    setCatalogError('');
    void listTemplates(workspace.id, offset, PAGE_SIZE, controller.signal).then(result => {
      if (!controller.signal.aborted) setPage(result);
    }).catch(error => {
      if (!controller.signal.aborted) setCatalogError(errorMessage(error));
    });
    return () => controller.abort();
  }, [workspace.id, offset, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    setProjects([]);
    setProjectId('');
    if (destinationId) void listProjects(destinationId, controller.signal).then(items => {
      if (controller.signal.aborted) return;
      setProjects(items);
      setProjectId(items[0]?.id ?? '');
    }).catch(error => { if (!controller.signal.aborted) setCopyError(errorMessage(error)); });
    return () => { controller.abort(); copyRequest.current?.abort(); };
  }, [destinationId]);

  const submit = async (event: FormEvent<HTMLFormElement>, template: WorkflowTemplate) => {
    event.preventDefault();
    if (busy || !destinationId) return;
    const fields = new FormData(event.currentTarget);
    const controller = new AbortController();
    copyRequest.current = controller;
    setCopyError('');
    setBusy(true);
    try {
      const target = projectId || (await ensureProject(destinationId, String(fields.get('repository')), null, controller.signal)).id;
      const workflow = await copyTemplate(template.id, target, String(fields.get('name')), controller.signal);
      if (!controller.signal.aborted) nav(`/editor?workflow=${workflow.id}`);
    } catch (error) {
      if (!controller.signal.aborted) setCopyError(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      if (copyRequest.current === controller) copyRequest.current = undefined;
    }
  };

  const update = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editing || busy) return;
    setBusy(true);
    setCopyError('');
    try {
      const updated = await editTemplate(editing.id, { name: editing.name, description: editing.description });
      setPage(current => current && { ...current, items: current.items.map(item => item.id === updated.id ? updated : item) });
      setEditing(null);
    } catch (error) {
      setCopyError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (template: WorkflowTemplate) => {
    if (busy || !window.confirm(`Удалить шаблон «${template.name}»?`)) return;
    setBusy(true);
    setCopyError('');
    try {
      await removeTemplate(template.id);
      setSelectedId('');
      setEditing(null);
      if (offset > 0 && page?.items.length === 1) setOffset(offset - PAGE_SIZE);
      else setRefresh(value => value + 1);
    } catch (error) {
      setCopyError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const publish = async (template: WorkflowTemplate) => {
    if (busy) return;
    setBusy(true);
    setCopyError('');
    try {
      await withWorkspaceLock(() => publishDraft(template.id, workspace.id));
      setRefresh(value => value + 1);
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
      {page?.total === 0 && <div className="empty-state"><BookMarked size={32} /><h2>Пока нет шаблонов</h2><p className="muted">Сохраните сценарий как шаблон в редакторе.</p></div>}
      <div className="template-grid">
      {page?.items.map(template => {
        const selected = selectedId === template.id;
        const names = new Map(template.graph.nodes.map(node => [node.id, types.find(type => type.kind === node.type)?.label ?? node.type]));
        return <article className="panel template-card" data-expanded={selected} key={template.id}>
          <GraphPreview graph={template.graph} />
          <div className="template-overview">
            <span className="template-scope">{template.is_builtin ? <BookMarked size={14} /> : template.can_edit ? <LockKeyhole size={14} /> : <UsersRound size={14} />}
              {template.is_builtin ? 'Встроенный шаблон' : template.can_edit ? 'Личный черновик' : 'Командный шаблон'}</span>
            <h2>{template.name}</h2>
            {template.description && <p className="template-description">{template.description}</p>}
            <div className="template-meta"><span><Workflow size={14} />Блоков: {template.graph.nodes.length}</span>
              {template.created_at && <span><CalendarDays size={14} />{new Date(template.created_at).toLocaleDateString('ru-RU')}</span>}</div>
            <button className="btn secondary" aria-expanded={selected} disabled={busy} onClick={() => {
              setSelectedId(selected ? '' : template.id);
              setEditing(null);
              setCopyError('');
            }}>{selected ? 'Свернуть' : 'Просмотреть'}{selected ? <ChevronUp size={16} /> : <ChevronDown size={16} />}</button>
          </div>
          {selected && editing?.id === template.id && (
            <form className="account-form template-details" onSubmit={event => void update(event)}>
              <label>Название<input value={editing.name} maxLength={120} required
                onChange={event => setEditing({ ...editing, name: event.target.value })} /></label>
              <label>Описание<textarea value={editing.description} maxLength={1000}
                onChange={event => setEditing({ ...editing, description: event.target.value })} /></label>
              {copyError && <p className="notice error" role="alert">{copyError}</p>}
              <div className="toolbar-actions">
                <button className="btn primary" disabled={busy}>Сохранить</button>
                <button className="btn secondary" type="button" disabled={busy} onClick={() => { setEditing(null); setCopyError(''); }}>Отмена</button>
              </div>
            </form>
          )}
          {selected && editing?.id !== template.id && <div className="template-details">
            {template.graph.edges.length > 0 && <section><h3>Связи сценария</h3><ul className="template-connections">
              {template.graph.edges.map(edge => <li key={edge.id}>{names.get(edge.source)} <span className="muted">{edge.source_port} →</span> {names.get(edge.target)}</li>)}
            </ul></section>}
            {template.schedule && <p className="muted">Параметры расписания будут скопированы. Включить расписание можно в редакторе.</p>}
            {template.can_edit && <div className="toolbar-actions">
              {!workspace.personal && workspace.role === 'owner' && template.draft_source_workspace_id &&
                <button className="btn primary" disabled={busy} onClick={() => void publish(template)}>Опубликовать в «{workspace.name}»</button>}
              {!template.draft_source_workspace_id && <p className="muted">Для публикации сохраните шаблон заново из исходного сценария.</p>}
              <button className="btn secondary" disabled={busy} onClick={() => { setEditing(template); setCopyError(''); }}>Изменить</button>
              <button className="btn secondary" disabled={busy} onClick={() => void remove(template)}>Удалить</button>
            </div>}
            {destination && <form className="template-panel" onSubmit={event => void submit(event, template)}>
              <h3>Создать копию сценария</h3>
              {canCopyToTeam && personal ?
                <label>Пространство для копии<select disabled={busy} value={destination.id} onChange={event => {
                  setCopyError('');
                  setCopyToPersonal(event.target.value === personal.id);
                }}>
                  <option value={workspace.id}>{workspace.name}</option>
                  <option value={personal.id}>{personal.name}</option>
                </select></label> : <p className="muted">Копия в пространстве «{destination.name}»</p>}
              <label>Название копии<input name="name" defaultValue={template.name} maxLength={120} required /></label>
              <label>Проект<select value={projectId} onChange={event => setProjectId(event.target.value)}>
                <option value="">Другой учебный репозиторий</option>
                {projects.map(project => <option key={project.id} value={project.id}>{project.name} · {project.repository_url}</option>)}
              </select></label>
              {!projectId && <label>URL репозитория<input name="repository" type="url" required placeholder="https://github.com/owner/repository" /></label>}
              {copyError && <p className="notice error" role="alert">{copyError}</p>}
              <button className="btn primary" disabled={busy}>{busy ? 'Копирование…' : 'Скопировать и открыть'}</button>
            </form>}
          </div>}
        </article>;
      })}
      </div>
      {page && page.total > PAGE_SIZE && <div className="toolbar-actions">
        <button className="btn secondary" disabled={offset === 0 || busy} onClick={() => setOffset(offset - PAGE_SIZE)}>Назад</button>
        <button className="btn secondary" disabled={offset + PAGE_SIZE >= page.total || busy} onClick={() => setOffset(offset + PAGE_SIZE)}>Дальше</button>
      </div>}
    </div>
  );
}
