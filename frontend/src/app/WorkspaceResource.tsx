import { useEffect, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { errorMessage } from '../shared/api/client';
import { getProject, getRun, getWorkflow } from '../shared/api/workflows';
import { useWorkspace } from './workspaceContext';
import { getLastRunId } from '../features/editor/defaults';

export function WorkspaceResource({ param, onSwitch, children }: {
  param: 'workflow' | 'run' | 'project'; onSwitch: (id: string) => void; children: ReactNode;
}) {
  const [params] = useSearchParams();
  const id = params.get(param) ?? (param === 'run' ? getLastRunId() : null);
  const { workspace, workspaces, switchLocked } = useWorkspace();
  const [result, setResult] = useState<{ resourceId: string; workspaceId?: string; error?: string }>();

  useEffect(() => {
    if (!id) {
      setResult(undefined);
      return;
    }
    const controller = new AbortController();
    const resolve = param === 'run' ? getRun : getWorkflow;
    const projectRequest = param === 'project' ? getProject(id, controller.signal)
      : resolve(id, controller.signal).then(resource => getProject(resource.project_id, controller.signal));
    void projectRequest.then(project => {
      if (!controller.signal.aborted) setResult({ resourceId: id, workspaceId: project.workspace_id });
    }).catch(failure => {
      if (!controller.signal.aborted) setResult({ resourceId: id, error: errorMessage(failure) });
    });
    return () => controller.abort();
  }, [id, param]);

  if (!id) return children;
  if (result?.resourceId !== id) return <p className="page" role="status">Проверяем рабочее пространство…</p>;
  if (result.error) return <p className="page notice error" role="alert">{result.error}</p>;
  if (result.workspaceId === workspace.id) return children;

  const target = workspaces.find(item => item.id === result.workspaceId);
  return <div className="page">
    <p role="status">Этот ресурс находится в другом рабочем пространстве.</p>
    {target && <button className="btn primary" disabled={switchLocked} onClick={() => onSwitch(target.id)}>Перейти в «{target.name}»</button>}
    {!target && <Link className="btn secondary" to="/">Открыть список репозиториев</Link>}
  </div>;
}
