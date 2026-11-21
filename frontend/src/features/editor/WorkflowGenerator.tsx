import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Bot, X } from 'lucide-react';
import { errorMessage } from '../../shared/api/client';
import { generateWorkflow, type GeneratedWorkflow } from '../../shared/api/workflows';
import { useEditorStore } from './store';

export function WorkflowGenerator() {
  const busy = useEditorStore(state => state.busy);
  const applyGenerated = useEditorStore(state => state.applyGenerated);
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const [candidate, setCandidate] = useState<GeneratedWorkflow | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (request.current || description.trim().length < 5 || busy) return;
    const controller = new AbortController();
    request.current = controller;
    setGenerating(true);
    setError('');
    setCandidate(null);
    try {
      const draft = await generateWorkflow(description.trim(), useEditorStore.getState().profileTimezone, controller.signal);
      if (controller.signal.aborted) return;
      const editor = useEditorStore.getState();
      if (editor.nodes.length || editor.workflowId || editor.busy) setCandidate(draft);
      else applyGenerated(draft);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setGenerating(false);
    }
  }

  return (
    <section className={`workflow-generator${open ? ' open' : ''}`} aria-label="Генерация сценария">
      <button type="button" className="btn secondary generator-toggle" aria-expanded={open}
        onClick={() => setOpen(value => !value)}>
        <Bot size={18} /> Создать по описанию {open && <X size={16} />}
      </button>
      {open && (
        <form className="generator-form" onSubmit={submit}>
          <label htmlFor="workflow-description">Какой сценарий нужно создать?</label>
          <textarea id="workflow-description" rows={4} maxLength={2000} value={description} disabled={generating}
            placeholder="Каждый день в 14:00 проверяй учебный репозиторий на секреты и присылай отчёт на почту"
            onChange={event => { setDescription(event.target.value); setCandidate(null); }} />
          {error && <p className="error-text" role="alert">{error}</p>}
          {generating && <p className="muted" role="status">Модель подбирает ноды и параметры…</p>}
          {candidate && (
            <p className="muted" role="status">Черновик «{candidate.name}» готов. Открытие заменит текущий холст.</p>
          )}
          {candidate
            ? <button type="button" className="btn primary" disabled={!!busy} onClick={() => applyGenerated(candidate)}>Открыть черновик</button>
            : <button type="submit" className="btn primary" disabled={generating || !!busy || description.trim().length < 5}>Создать черновик</button>}
        </form>
      )}
    </section>
  );
}
