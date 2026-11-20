import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Workflow as WorkflowIcon } from 'lucide-react';
import { errorMessage } from '../shared/api/client';
import { listProjects, listWorkflows, type Project, type Workflow } from '../shared/api/workflows';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { useEditorStore } from '../features/editor/store';

export function Scenarios() {
  const nav = useNavigate();
  const { types, source } = useNodeTypesStore();
  const newWorkflow = useEditorStore(s => s.newWorkflow);
  const [workflows, setWorkflows] = useState<(Workflow & { project: Project })[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const projects = (await listProjects()).items;
        const pages = await Promise.all(projects.map(async p => (await listWorkflows(p.id)).items.map(w => ({ ...w, project: p }))));
        setWorkflows(pages.flat().sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
      } catch (e) {
        setError(errorMessage(e));
        setWorkflows([]);
      }
    })();
  }, []);

  const analyzers = types.filter(t => t.kind.startsWith('scan_')).length;

  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Мои сценарии</h1>
          <p className="muted">Создавайте и управляйте автоматическими проверками безопасности</p>
        </div>
        <button className="btn primary" onClick={() => { newWorkflow(); nav('/editor'); }}>
          <Plus size={16} /> Новый сценарий
        </button>
      </div>
      {error && <div className="notice error">{error}</div>}
      <div className="summary-grid">
        <div className="panel"><span className="muted">Сценарии</span><h2>{workflows?.length ?? '…'}</h2></div>
        <div className="panel"><span className="muted">Доступные анализаторы</span><h2>{analyzers}</h2></div>
        <div className="panel"><span className="muted">Бэкенд</span><h2>{source === 'api' ? 'Подключён' : 'Недоступен'}</h2></div>
      </div>
      {workflows?.length === 0 && !error && (
        <div className="panel">Сохранённых сценариев пока нет. Откройте редактор и нажмите «Сохранить».</div>
      )}
      {workflows?.map(w => (
        <div key={w.id} className="panel workflow-card" onClick={() => nav(`/editor?workflow=${w.id}`)}>
          <WorkflowIcon size={24} />
          <div>
            <strong>{w.name}</strong>
            <p className="muted">{w.project.repository_url} · {w.graph.nodes.length} нод · изменён {new Date(w.updated_at).toLocaleString('ru-RU')}</p>
          </div>
          <span className="tag">Сохранён</span>
        </div>
      ))}
    </div>
  );
}
