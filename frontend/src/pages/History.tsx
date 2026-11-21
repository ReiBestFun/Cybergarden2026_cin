import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '../shared/api/client';
import { listProjects, listRuns, type Project, type Run } from '../shared/api/workflows';
import { useWorkspace } from '../app/workspaceContext';

const STATUS_LABEL: Record<string, string> = { queued: 'в очереди', running: 'выполняется', succeeded: 'успешно', partial: 'частично', failed: 'ошибка' };
const TRIGGER_LABEL: Record<Run['trigger'], string> = { manual: 'вручную', schedule: 'расписание', ci: 'GitHub Actions' };

export function History() {
  const nav = useNavigate();
  const { workspace } = useWorkspace();
  const [runs, setRuns] = useState<(Run & { project: Project })[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const projects = await listProjects(workspace.id, controller.signal);
        const pages = await Promise.all(projects.map(async p => (await listRuns(p.id, controller.signal)).map(r => ({ ...r, project: p }))));
        if (!controller.signal.aborted) setRuns(pages.flat().sort((a, b) => b.created_at.localeCompare(a.created_at)));
      } catch (e) {
        if (!controller.signal.aborted) { setError(errorMessage(e)); setRuns([]); }
      }
    })();
    return () => controller.abort();
  }, [workspace.id]);

  return (
    <div className="page">
      <h1>История запусков</h1>
      <p className="muted">Запуски в пространстве «{workspace.name}»</p>
      {error && <div className="notice error">{error}</div>}
      <div className="panel">
        {!runs ? <p className="muted">Загрузка…</p> : runs.length === 0 ? !error && <p className="muted">Запусков пока нет.</p> : (
          <table>
            <thead>
              <tr><th>Дата</th><th>Репозиторий</th><th>Триггер</th><th>Статус</th><th>Находки</th><th>Коммит</th></tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id} className="clickable" onClick={() => nav(`/results?run=${r.id}`)}>
                  <td>{new Date(r.created_at).toLocaleString('ru-RU')}</td>
                  <td>{r.project.name}</td>
                  <td>{TRIGGER_LABEL[r.trigger]}</td>
                  <td><span className={`tag run-${r.status}`}>{STATUS_LABEL[r.status]}</span></td>
                  <td>{r.summary?.total ?? '—'}</td>
                  <td>{r.commit_sha ? <code>{r.commit_sha.slice(0, 7)}</code> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
