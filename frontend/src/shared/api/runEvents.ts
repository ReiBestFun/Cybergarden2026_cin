import { apiUrl } from './client';
import { getRun, isFinished, type RunDetail } from './workflows';

export function watchRun(
  id: string, signal: AbortSignal, onRun: (run: RunDetail) => void,
  onReconnect: (reconnecting: boolean) => void, waitForDelivery = false,
): Promise<RunDetail> {
  const finished = (run: RunDetail) => isFinished(run.status) && (!waitForDelivery || !Object.values(run.delivery ?? {}).some(
    delivery => delivery.recipients?.length && (delivery.status === 'queued' || delivery.status === 'sending'),
  ));
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const source = new EventSource(apiUrl(`/api/runs/${id}/events${waitForDelivery ? '?wait_for_delivery=true' : ''}`));
    const close = () => {
      source.close();
      signal.removeEventListener('abort', abort);
    };
    const abort = () => { close(); reject(signal.reason); };
    signal.addEventListener('abort', abort, { once: true });
    source.onopen = () => onReconnect(false);
    source.addEventListener('run', event => {
      try {
        const run: RunDetail = JSON.parse(event.data);
        onRun(run);
        if (finished(run)) { close(); resolve(run); }
      } catch (error) {
        close();
        reject(error);
      }
    });
    source.onerror = () => {
      if (source.readyState !== EventSource.CLOSED) { onReconnect(true); return; }
      close();
      // Recover a terminal state or surface the API error if SSE could not be opened.
      void getRun(id).then(run => {
        if (signal.aborted) throw signal.reason;
        if (!finished(run)) throw new Error('Не удалось открыть поток событий запуска.');
        onRun(run);
        resolve(run);
      }).catch(reject);
    };
  });
}
