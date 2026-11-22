import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Save, Download, Play, X, FilePlus } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';
import { NodePalette } from './NodePalette';
import { Canvas } from './Canvas';
import { SettingsPanel } from './SettingsPanel';
import { CiSetup } from './CiSetup';
import { PublishTemplate } from './PublishTemplate';
import { WorkflowGenerator } from './WorkflowGenerator';
import { ImportMcpDraft } from './ImportMcpDraft';
import { toApiGraph } from './graph';
import { useWorkspace } from '../../app/workspaceContext';

function download(blob: Blob, filename: string) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

export function Editor() {
  const { workspace } = useWorkspace();
  const editable = workspace.role === 'owner';
  const { nodes, edges, project, name, workflowId, busy, notice, run, canvasRevision, save, runWorkflow, loadWorkflow, newWorkflow, setNotice } = useEditorStore();
  const types = useNodeTypesStore(s => s.types);
  const [params, setParams] = useSearchParams();
  const requestedWorkflowId = params.get('workflow');
  const [openedLink, setOpenedLink] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const hasCiTrigger = nodes.some(node => node.data.kind === 'ci_trigger');

  useEffect(() => {
    if (requestedWorkflowId && useEditorStore.getState().workflowId !== requestedWorkflowId) {
      void loadWorkflow(requestedWorkflowId);
    }
  }, [requestedWorkflowId, loadWorkflow]);

  useEffect(() => {
    if (!requestedWorkflowId || requestedWorkflowId === workflowId) setOpenedLink(requestedWorkflowId);
  }, [requestedWorkflowId, workflowId]);

  if (requestedWorkflowId && openedLink !== requestedWorkflowId && workflowId !== requestedWorkflowId) {
    return <main className="page">
      {notice?.kind === 'error' && !busy ? <>
        <p role="alert" className="notice error">{notice.text}</p>
        <button className="btn secondary" onClick={() => void loadWorkflow(requestedWorkflowId)}>Повторить загрузку</button>
      </> : <p role="status">Загружаем сценарий и расписание…</p>}
    </main>;
  }

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ name, repository_url: project, graph: toApiGraph(nodes, edges, types) }, null, 2)], { type: 'application/json' });
    download(blob, 'cyberflow-workflow.json');
  };

  return (
    <div className="editor-page">
      <header className="editor-toolbar">
        <div>
          <h1>Редактор сценария</h1>
          <span className="muted">{name} · {workflowId ? 'сохранён на сервере' : 'не сохранён'}</span>
        </div>
        {editable && <div className="toolbar-actions">
          <button className="btn secondary" onClick={() => {
            newWorkflow();
            setParams({}, { replace: true });
          }} disabled={!!busy} title="Новый сценарий">
            <FilePlus size={16} /> Новый
          </button>
          <button className="btn secondary" onClick={() => void save()} disabled={!!busy}>
            <Save size={16} /> {busy === 'save' ? 'Сохранение…' : 'Сохранить'}
          </button>
          <button className="btn secondary" onClick={exportJson}>
            <Download size={16} /> JSON
          </button>
          <ImportMcpDraft onImported={() => setParams({}, { replace: true })} />
          <button className="btn secondary" disabled={!!busy} aria-expanded={publishing} onClick={() => setPublishing(value => !value)}>В шаблоны</button>
          {!hasCiTrigger && <button className="btn primary" disabled={!!busy} onClick={() => void runWorkflow()}>
            <Play size={16} /> {busy === 'run' ? 'Выполнение…' : 'Запустить'}
          </button>}
        </div>}
      </header>

      {notice && (
        <div className={`notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
          <span>{notice.text}</span>
          {run && !busy && run.status !== 'failed' && <Link to={`/results?run=${run.id}`}>Открыть результаты</Link>}
          <button className="icon-btn" aria-label="Скрыть" onClick={() => setNotice(null)}><X size={15} /></button>
        </div>
      )}

      {editable && hasCiTrigger && <CiSetup key={project} onDownload={download} />}
      {editable && publishing && <PublishTemplate onClose={() => setPublishing(false)} />}

      <div className={`editor-layout${editable ? '' : ' editor-readonly'}`}>
        {editable && <NodePalette />}
        <Canvas editable={editable} />
        {editable && <SettingsPanel />}
      </div>

      {editable && <WorkflowGenerator key={canvasRevision} />}
    </div>
  );
}
