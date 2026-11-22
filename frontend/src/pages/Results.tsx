import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Download } from 'lucide-react';
import { getLastRunId } from '../features/editor/defaults';
import { NodeInspector } from '../features/editor/NodeInspector';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { errorMessage } from '../shared/api/client';
import { watchRun } from '../shared/api/runEvents';
import { exportUrl, getReport, isFinished, type RunDetail, type RunReport } from '../shared/api/workflows';
import { CATEGORY_LABEL, RunFindings } from './RunFindings';

const STATUS_LABEL: Record<string, string> = { queued: 'в очереди', running: 'выполняется', succeeded: 'успешно', partial: 'частичное покрытие', failed: 'ошибка' };
const CI_LABEL: Record<string, string> = {
  passed: 'Проверка CI пройдена', blocked: 'CI заблокирован по критичности', error: 'Проверка CI завершилась ошибкой',
};
const DELIVERY_LABEL: Record<string, string> = {
  queued: 'в очереди', sending: 'отправляется', accepted: 'принято провайдером',
  sent: 'отправлено', succeeded: 'отправлено', failed: 'ошибка отправки',
  partial: 'частичная отправка', unknown: 'результат неизвестен', skipped: 'пропущено', cancelled: 'отменено',
};
const DELIVERY_CHANNEL_LABEL: Record<string, string> = { email: 'Почта', telegram: 'Telegram', slack: 'Slack' };

export function Results() {
  const [params] = useSearchParams();
  const runId = params.get('run') ?? getLastRunId();
  return <RunResults key={runId} runId={runId} />;
}

function RunResults({ runId }: { runId: string | null }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [report, setReport] = useState<RunReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [selected, setSelected] = useState('');
  const types = useNodeTypesStore(state => state.types);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    let loadingReport = false;
    const handleError = (error: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(error));
    };
    void watchRun(runId, controller.signal, detail => {
      setRun(detail);
      if (!isFinished(detail.status) || loadingReport) return;
      loadingReport = true;
      void getReport(runId, controller.signal).then(report => {
        if (controller.signal.aborted) return;
        setReport(report);
      }).catch(handleError);
    }, setReconnecting, true).catch(handleError);
    return () => controller.abort();
  }, [runId]);

  const finished = run && isFinished(run.status);
  const inspected = run?.nodes.find(node => node.node_id === selected);
  const gate = run?.nodes.find(node => node.type === 'ci_gate')?.output_preview;
  const decision = typeof gate?.decision === 'string' ? gate.decision : null;
  const checks = Object.entries(report?.coverage.checks ?? {}).filter(([, check]) => check.warnings.length);
  const deliveries = Object.entries(run?.delivery ?? report?.delivery ?? {});

  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Результаты проверок</h1>
          <p className="muted">
            {run ? <>Запуск от {new Date(run.created_at).toLocaleString('ru-RU')} · {STATUS_LABEL[run.status]}{run.source_snapshot.archive_sha256 && <> · ZIP <code title={run.source_snapshot.archive_sha256}>{run.source_snapshot.archive_sha256.slice(0, 12)}</code></>}{run.commit_sha && <> · коммит <code>{run.commit_sha.slice(0, 7)}</code></>}</>
              : runId ? (error ? 'Не удалось загрузить запуск.' : 'Загружаем данные запуска…') : 'Выберите запуск в истории рабочего пространства.'}
          </p>
        </div>
        {run && finished && run.status !== 'failed' && (
          <div className="toolbar-actions">
            <a className="btn secondary" href={exportUrl(run.id, 'json')} download={`findings-${run.id}.json`}><Download size={16} /> JSON</a>
            <a className="btn secondary" href={exportUrl(run.id, 'markdown')} download={`report-${run.id}.md`}><Download size={16} /> Markdown</a>
          </div>
        )}
      </div>

      {!runId && <Link className="btn secondary" to="/history">Открыть историю запусков</Link>}
      {error && <div className="notice error">{error}</div>}
      {reconnecting && <div className="notice info" role="status">Восстанавливаем поток событий…</div>}
      {finished && decision && CI_LABEL[decision] && (
        <div className={`notice ${decision === 'passed' ? 'success' : 'error'}`}>
          {CI_LABEL[decision]}{typeof gate?.threshold === 'string' && <> · порог {gate.threshold}</>}
        </div>
      )}

      {run?.error && (
        <div className="notice error">
          {run.error.message}{run.error.hint && <p>{run.error.hint}</p>}
        </div>
      )}

      {(report?.llm?.text || report?.llm?.error) && (
        <section className="panel">
          <h3>AI-рекомендации</h3>
          <p style={{ whiteSpace: 'pre-line' }}>{report.llm.text ?? report.llm.error?.message}</p>
        </section>
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

      {deliveries.length > 0 && (
        <div className="panel">
          <h3>Доставка оповещений</h3>
          {deliveries.map(([nodeId, delivery]) => (
            <section key={nodeId}>
              <h4>{DELIVERY_CHANNEL_LABEL[delivery.channel ?? ''] ?? nodeId}</h4>
              <p>{DELIVERY_LABEL[delivery.status] ?? delivery.status}</p>
              {delivery.error && <p className="muted">{delivery.error.message}</p>}
              {delivery.recipients && (
                <ul>
                  {delivery.recipients.map((item, index) => (
                    <li key={item.id ?? index}>
                      <code>{item.recipient}</code>{' · '}
                      {DELIVERY_LABEL[item.status] ?? item.status}
                      {item.phase === 'attachment' && ' · этап: вложение'}
                      {item.error && <p className="muted">{item.error}</p>}
                    </li>
                  ))}
                </ul>
              )}
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

      {finished && run.status !== 'failed' && <RunFindings key={run.id} runId={run.id} />}
    </div>
  );
}
