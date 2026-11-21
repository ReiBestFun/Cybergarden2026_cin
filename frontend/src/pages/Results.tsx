import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Download } from 'lucide-react';
import { getLastRunId } from '../features/editor/defaults';
import { errorMessage } from '../shared/api/client';
import { exportUrl, getReport, getRun, listFindings, type Finding, type RunDetail, type RunReport } from '../shared/api/workflows';

// The API returns lowercase values; the report format uses SECRET/SCA/SAST and uppercase severity.
const SEVERITY_STYLE: Record<string, { background: string; color: string }> = {
  critical: { background: '#fde2e2', color: '#7f1d1d' },
  high: { background: '#fee2e2', color: '#991b1b' },
  medium: { background: '#fef3c7', color: '#92400e' },
  low: { background: '#e0f2fe', color: '#075985' },
};
const CATEGORY_LABEL: Record<string, string> = { secrets: 'SECRET', sca: 'SCA', sast: 'SAST' };
const STATUS_LABEL: Record<string, string> = { queued: 'в очереди', running: 'выполняется', succeeded: 'успешно', partial: 'частичное покрытие', failed: 'ошибка' };

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
  const [report, setReport] = useState<RunReport | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [category, setCategory] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    setRun(null);
    setReport(null);
    setFindings([]);
    setError(null);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const detail = await getRun(runId);
        if (cancelled) return;
        setRun(detail);
        if (detail.status === 'queued' || detail.status === 'running') { timer = setTimeout(load, 1500); return; }
        const [page, details] = await Promise.all([listFindings(runId), getReport(runId)]);
        if (!cancelled) {
          setFindings(page.items);
          setReport(details);
        }
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [runId]);

  const shown = category ? findings.filter(f => f.category === category) : findings;
  const finished = run && run.status !== 'queued' && run.status !== 'running';
  const checks = Object.entries(report?.coverage.checks ?? {}).filter(([, check]) => check.warnings.length);

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
      {run?.error && (
        <div className="notice error">
          {run.error.message}{run.error.hint && <p>{run.error.hint}</p>}
        </div>
      )}

      {checks.length > 0 && (
        <div className="panel">
          <h3>Диагностика проверок</h3>
          {checks.map(([id, check]) => (
            <section key={id}>
              <h4>{CATEGORY_LABEL[check.category]}</h4>
              <ul>
                {check.warnings.map((warning, index) => (
                  <li key={warning.code + '-' + index}>
                    {warning.message}{' '}
                    {warning.path && <code>{warning.path}{warning.line ? ':' + warning.line : ''}</code>}
                    {!warning.path && warning.field && <code>{warning.field}</code>}
                    {warning.hint && <p className="muted">{warning.hint}</p>}
                  </li>
                ))}
              </ul>
            </section>
          ))}
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
