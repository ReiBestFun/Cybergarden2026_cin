import { useEffect, useState } from 'react';
import { errorMessage } from '../shared/api/client';
import { listFindings, type Finding, type FindingVerification, type Page } from '../shared/api/workflows';

// The API returns lowercase values; the report format uses SECRET/SCA/SAST and uppercase severity.
const SEVERITY_STYLE: Record<string, { background: string; color: string }> = {
  critical: { background: '#fde2e2', color: '#7f1d1d' },
  high: { background: '#fee2e2', color: '#991b1b' },
  medium: { background: '#fef3c7', color: '#92400e' },
  low: { background: '#e0f2fe', color: '#075985' },
};
export const CATEGORY_LABEL: Record<Finding['category'], string> = { secrets: 'SECRET', sca: 'SCA', sast: 'SAST' };
const VERIFICATION_LABEL: Record<FindingVerification['status'], string> = {
  confirmed: 'Подтверждено', needs_review: 'Нужна проверка', false_positive: 'Ложное срабатывание',
};
function findingName(f: Finding) {
  if (f.category === 'sca') {
    const m = f.metadata;
    const dep = [m.group, m.artifact].filter(Boolean).join(':');
    return [f.title, dep && `${dep}${m.version ? ` ${m.version}` : ''}`, m.advisory_id].filter(Boolean).join(' · ');
  }
  return f.title;
}

export function RunFindings({ runId }: { runId: string }) {
  const [page, setPage] = useState<Page<Finding> | null>(null);
  const [category, setCategory] = useState('');
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    listFindings(runId, category, offset, controller.signal)
      .then(value => { if (!controller.signal.aborted) setPage(value); })
      .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [runId, category, offset]);

  function changePage(next: number) {
    setPage(null);
    setOffset(next);
  }

  return (
    <div className="panel run-findings">
      <div className="panel-heading">
        <h3 id="findings-heading">Находки</h3>
        <select value={category} onChange={e => { setPage(null); setCategory(e.target.value); setOffset(0); }} aria-label="Категория">
          <option value="">Все категории</option>
          {Object.entries(CATEGORY_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      {error ? <p className="notice error" role="alert">{error}</p> : !page ? <p role="status">Загружаем находки…</p> : page.items.length ? (
        <div className="table-scroll"><table aria-labelledby="findings-heading">
          <thead>
            <tr><th scope="col">Категория</th><th scope="col">Находка</th><th scope="col">Критичность</th><th scope="col">Путь</th><th scope="col">Строка</th><th scope="col">Дополнительная проверка</th></tr>
          </thead>
          <tbody>
            {page.items.map((f) => (
              <tr key={f.id}>
                <td className="finding-category">{CATEGORY_LABEL[f.category] ?? f.category}</td>
                <td className="finding-title"><details className="finding-details">
                  <summary>{findingName(f)}</summary>
                  <p>{f.description}</p>
                  <p><strong>Как исправить</strong><br />{f.remediation}</p>
                </details><small className="muted">{f.metadata.class ?? f.rule_id}</small></td>
                <td className="finding-severity"><span className="tag" style={SEVERITY_STYLE[f.severity]}>{f.severity.toUpperCase()}</span></td>
                <td className="finding-path"><span className="cell-caption" aria-hidden="true">Путь</span><code>{f.path}</code></td>
                <td className="finding-line"><span className="cell-caption" aria-hidden="true">Строка</span>{f.line ?? '—'}{f.end_line && f.end_line !== f.line ? `–${f.end_line}` : ''}</td>
                <td className="finding-verification"><span className="cell-caption" aria-hidden="true">Дополнительная проверка</span>
                  {f.verification ? <>
                    {VERIFICATION_LABEL[f.verification.status]}<br />
                    <small className="muted">{f.verification.message}</small>
                  </> : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      ) : <p className="muted">{category ? 'В этой категории находок нет.' : 'Находок нет.'}</p>}
      {page && page.total > page.limit && <div className="toolbar-actions">
        <button className="btn secondary" disabled={offset === 0} onClick={() => changePage(offset - page.limit)}>Назад</button>
        <span>{offset + 1}–{offset + page.items.length} из {page.total}</span>
        <button className="btn secondary" disabled={offset + page.items.length >= page.total} onClick={() => changePage(offset + page.limit)}>Далее</button>
      </div>}
    </div>
  );
}
