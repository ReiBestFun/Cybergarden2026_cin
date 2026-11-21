import { useEffect, useRef, useState } from 'react';
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
import { toApiGraph } from './graph';
import { errorMessage } from '../../shared/api/client';
import { getSecurityWorkflow } from '../../shared/api/workflows';
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
  const exportRequest = useRef<AbortController>();
  const [downloadingCi, setDownloadingCi] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const hasCiTrigger = nodes.some(node => node.data.kind === 'ci_trigger');

  useEffect(() => {
    if (requestedWorkflowId && useEditorStore.getState().workflowId !== requestedWorkflowId) {
      void loadWorkflow(requestedWorkflowId);
    }
  }, [requestedWorkflowId, loadWorkflow]);

  useEffect(() => () => exportRequest.current?.abort(), []);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ name, repository_url: project, graph: toApiGraph(nodes, edges, types) }, null, 2)], { type: 'application/json' });
    download(blob, 'cyberflow-workflow.json');
  };

  const exportCi = async () => {
    const controller = new AbortController();
    exportRequest.current = controller;
    setDownloadingCi(true);
    try {
      const id = await save();
      if (!id || controller.signal.aborted) return;
      const file = await getSecurityWorkflow(id, 'local', controller.signal);
      if (!controller.signal.aborted) download(file, 'security.yml');
    } catch (error) {
      if (!controller.signal.aborted) setNotice({ kind: 'error', text: errorMessage(error) });
    } finally {
      if (!controller.signal.aborted) setDownloadingCi(false);
      if (exportRequest.current === controller) exportRequest.current = undefined;
    }
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
          <button className="btn secondary" disabled={!!busy} aria-expanded={publishing} onClick={() => setPublishing(value => !value)}>В шаблоны</button>
          {hasCiTrigger && (
            <button className="btn secondary" disabled={!!busy || downloadingCi} onClick={() => void exportCi()}>
              <Download size={16} /> security.yml
            </button>
          )}
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

      {editable && hasCiTrigger && <CiSetup key={project} />}
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
