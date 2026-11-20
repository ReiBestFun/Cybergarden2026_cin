import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '../shared/api/client';
import { listProjects, listRuns, type Project, type Run } from '../shared/api/workflows';

const STATUS_LABEL: Record<string, string> = { queued: 'в очереди', running: 'выполняется', succeeded: 'успешно', partial: 'частично', failed: 'ошибка' };

export function History() {
  const nav = useNavigate();
  const [runs, setRuns] = useState<(Run & { project: Project })[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const projects = (await listProjects()).items;
        const pages = await Promise.all(projects.map(async p => (await listRuns(p.id)).items.map(r => ({ ...r, project: p }))));
        setRuns(pages.flat().sort((a, b) => b.created_at.localeCompare(a.created_at)));
      } catch (e) {
        setError(errorMessage(e));
      }
    })();
  }, []);

  return (
    <div className="page">
      <h1>История запусков</h1>
      <p className="muted">Все запуски сценариев, сохранённые на бэкенде</p>
      {error && <div className="notice error">{error}</div>}
      <div className="panel">
        {!runs ? <p className="muted">Загрузка…</p> : runs.length === 0 ? <p className="muted">Запусков пока нет.</p> : (
          <table>
            <thead>
              <tr><th>Дата</th><th>Репозиторий</th><th>Триггер</th><th>Статус</th><th>Находки</th><th>Коммит</th></tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id} className="clickable" onClick={() => nav(`/results?run=${r.id}`)}>
                  <td>{new Date(r.created_at).toLocaleString('ru-RU')}</td>
                  <td>{r.project.name}</td>
                  <td>{r.trigger === 'manual' ? 'вручную' : 'расписание'}</td>
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
