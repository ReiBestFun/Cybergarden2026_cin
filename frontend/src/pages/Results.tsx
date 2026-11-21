import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Download } from 'lucide-react';
import { getLastRunId } from '../features/editor/defaults';
import { NodeInspector } from '../features/editor/NodeInspector';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { errorMessage } from '../shared/api/client';
import { watchRun } from '../shared/api/runEvents';
import { exportUrl, isFinished, listFindings, type Finding, type RunDetail } from '../shared/api/workflows';

// The API returns lowercase values; the report format uses SECRET/SCA/SAST and uppercase severity.
const SEVERITY_STYLE: Record<string, { background: string; color: string }> = {
  critical: { background: '#fde2e2', color: '#7f1d1d' },
  high: { background: '#fee2e2', color: '#991b1b' },
  medium: { background: '#fef3c7', color: '#92400e' },
  low: { background: '#e0f2fe', color: '#075985' },
};
const CATEGORY_LABEL: Record<string, string> = { secrets: 'SECRET', sca: 'SCA', sast: 'SAST' };
const STATUS_LABEL: Record<string, string> = { queued: 'в очереди', running: 'выполняется', succeeded: 'успешно', partial: 'частичное покрытие', failed: 'ошибка' };
const CI_LABEL: Record<string, string> = {
  passed: 'Проверка CI пройдена', blocked: 'CI заблокирован по критичности', error: 'Проверка CI завершилась ошибкой',
};

function findingName(f: Finding) {
  if (f.category === 'sca') {
    const m = f.metadata;
    const dep = [m.group, m.artifact].filter(Boolean).join(':');
    return [f.title, dep && `${dep}${m.version ? ` ${m.version}` : ''}`, m.advisory_id].filter(Boolean).join(' · ');
  }
  return f.title;
}

export function Results() {
  const [params] = useSearchParams();
  const runId = params.get('run') ?? getLastRunId();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [category, setCategory] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [selected, setSelected] = useState('');
  const types = useNodeTypesStore(state => state.types);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    setRun(null);
    setSelected('');
    setError(null);
    setFindings([]);
    setReconnecting(false);
    void watchRun(runId, controller.signal, setRun, setReconnecting).then(async run => {
      if (run.status === 'failed' || controller.signal.aborted) return;
      const page = await listFindings(runId);
      if (!controller.signal.aborted) setFindings(page.items);
    }).catch(error => {
      if (!controller.signal.aborted) setError(errorMessage(error));
    });
    return () => controller.abort();
  }, [runId]);

  const shown = category ? findings.filter(f => f.category === category) : findings;
  const finished = run && isFinished(run.status);
  const inspected = run?.nodes.find(node => node.node_id === selected);
  const gate = run?.nodes.find(node => node.type === 'ci_gate')?.output_preview;
  const decision = typeof gate?.decision === 'string' ? gate.decision : null;

  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Результаты проверок</h1>
          <p className="muted">
            {run ? <>Запуск от {new Date(run.created_at).toLocaleString('ru-RU')} · {STATUS_LABEL[run.status]}{run.commit_sha && <> · коммит <code>{run.commit_sha.slice(0, 7)}</code></>}</>
              : 'Данные последнего запуска из API'}
          </p>
        </div>
        {run && finished && run.status !== 'failed' && (
          <div className="toolbar-actions">
            <a className="btn secondary" href={exportUrl(run.id, 'json')} download={`findings-${run.id}.json`}><Download size={16} /> JSON</a>
            <a className="btn secondary" href={exportUrl(run.id, 'markdown')} download={`report-${run.id}.md`}><Download size={16} /> Markdown</a>
          </div>
        )}
      </div>

      {!runId && <div className="panel">Запусков пока нет. <Link to="/editor">Запустите сценарий в редакторе.</Link></div>}
      {error && <div className="notice error">{error}</div>}
      {reconnecting && <div className="notice info" role="status">Восстанавливаем поток событий…</div>}
      {run?.error && <div className="notice error">{run.error.message}</div>}
      {finished && decision && CI_LABEL[decision] && (
        <div className={`notice ${decision === 'passed' ? 'success' : 'error'}`}>
          {CI_LABEL[decision]}{typeof gate?.threshold === 'string' && <> · порог {gate.threshold}</>}
        </div>
      )}

      {run?.summary && (
        <div className="summary-grid">
          <div className="panel"><span className="muted">Всего находок</span><h2>{run.summary.total}</h2></div>
          <div className="panel"><span className="muted">High</span><h2>{run.summary.high_count}</h2></div>
          <div className="panel"><span className="muted">Critical</span><h2>{run.summary.critical_count}</h2></div>
        </div>
      )}

      {run && !finished && <div className="panel">Запуск ещё выполняется…</div>}
      {run && run.nodes.length > 0 && <section className="panel run-inspector">
        <label>Данные ноды{' '}
          <select value={selected} onChange={event => setSelected(event.target.value)}>
            <option value="">Выбрать ноду</option>
            {run.nodes.map(node => <option key={node.node_id} value={node.node_id}>
              {types.find(type => type.kind === node.type)?.label ?? node.type} · {node.node_id}
            </option>)}
          </select>
        </label>
        {inspected && <NodeInspector key={`${run.id}:${inspected.node_id}`} node={inspected} />}
      </section>}

      {finished && run.status !== 'failed' && (
        <div className="panel">
          <div className="panel-heading">
            <h3>Находки</h3>
            <select value={category} onChange={e => setCategory(e.target.value)} aria-label="Категория">
              <option value="">Все категории</option>
              <option value="secrets">SECRET</option>
              <option value="sca">SCA</option>
              <option value="sast">SAST</option>
            </select>
          </div>
          {shown.length ? (
            <table>
              <thead>
                <tr><th>Категория</th><th>Находка</th><th>Критичность</th><th>Путь</th><th>Строка</th></tr>
              </thead>
              <tbody>
                {shown.map((f) => (
                  <tr key={f.id} title={f.remediation}>
                    <td>{CATEGORY_LABEL[f.category] ?? f.category}</td>
                    <td>{findingName(f)}<br /><small className="muted">{f.metadata.class ?? f.rule_id}</small></td>
                    <td><span className="tag" style={SEVERITY_STYLE[f.severity]}>{f.severity.toUpperCase()}</span></td>
                    <td><code>{f.path}</code></td>
                    <td>{f.line ?? '—'}{f.end_line && f.end_line !== f.line ? `–${f.end_line}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted">Находок нет.</p>}
        </div>
      )}
    </div>
  );
}
