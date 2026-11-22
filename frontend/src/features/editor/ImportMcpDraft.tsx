import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Upload } from 'lucide-react';
import { useWorkspace } from '../../app/workspaceContext';
import { errorMessage } from '../../shared/api/client';
import { validateImportedDraft } from '../../shared/api/mcpAccess';
import { useEditorStore } from './store';

const MAX_DRAFT_BYTES = 256 * 1024;

export function ImportMcpDraft({ onImported }: { onImported: () => void }) {
  const { workspace } = useWorkspace();
  const busy = useEditorStore(state => state.busy);
  const input = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController>();
  const [pending, setPending] = useState(false);

  useEffect(() => () => request.current?.abort(), []);

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || pending || busy) return;
    const editor = useEditorStore.getState();
    const controller = new AbortController();
    request.current = controller;
    const isCurrent = () => {
      const state = useEditorStore.getState();
      return !controller.signal.aborted && state.workspaceId === workspace.id && state.canvasRevision === editor.canvasRevision;
    };
    setPending(true);
    try {
      if (file.size > MAX_DRAFT_BYTES) throw new Error('Выберите JSON-файл размером до 256 КБ.');
      let draft: unknown;
      try {
        draft = JSON.parse(await file.text());
      } catch {
        throw new Error('Файл должен содержать корректный JSON.');
      }
      const graph = draft && typeof draft === 'object' && 'graph' in draft ? draft.graph : draft;
      const validated = await validateImportedDraft(workspace.id, graph, controller.signal);
      if (!isCurrent()) return;
      const current = useEditorStore.getState();
      if (current.busy || (current.hasUnsavedChanges() && !window.confirm('Заменить несохранённый черновик содержимым файла?'))) return;
      current.newWorkflow();
      current.applyGenerated({ name: 'Черновик из MCP', graph: validated, schedule: null, repository_url: null, issues: [] });
      current.setSchedule({ enabled: false });
      current.setNotice({ kind: 'info', text: 'Черновик открыт. Проверьте репозиторий и параметры нод перед сохранением.' });
      onImported();
    } catch (failure) {
      if (isCurrent()) editor.setNotice({ kind: 'error', text: errorMessage(failure) });
    } finally {
      if (request.current === controller) request.current = undefined;
      if (!controller.signal.aborted) setPending(false);
    }
  }

  return <>
    <input type="file" hidden ref={input} accept=".json,application/json" aria-label="JSON-файл черновика"
      onChange={event => void importFile(event)} />
    <button type="button" className="btn secondary" disabled={!!busy || pending} onClick={() => input.current?.click()}>
      <Upload size={16} /> {pending ? 'Проверяем…' : 'Импорт JSON'}
    </button>
  </>;
}
