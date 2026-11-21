import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { BookMarked, Save, Download, Play, Bot, X, MessageCircle, Send, FilePlus } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';
import { NodePalette } from './NodePalette';
import { Canvas } from './Canvas';
import { SettingsPanel } from './SettingsPanel';
import { CiSetup } from './CiSetup';
import { toApiGraph } from './graph';
import { errorMessage } from '../../shared/api/client';
import { createTemplate } from '../../shared/api/account';
import { getSecurityWorkflow } from '../../shared/api/workflows';

function download(blob: Blob, filename: string) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

export function Editor() {
  const { nodes, edges, project, name, workflowId, busy, notice, run, save, runWorkflow, loadWorkflow, newWorkflow, setNotice } = useEditorStore();
  const types = useNodeTypesStore(s => s.types);
  const [params, setParams] = useSearchParams();
  const [downloadingCi, setDownloadingCi] = useState(false);
  const [templateName, setTemplateName] = useState<string | null>(null);
  const [templateDescription, setTemplateDescription] = useState('');
  const [savingTemplate, setSavingTemplate] = useState(false);
  const hasCiTrigger = nodes.some(node => node.data.kind === 'ci_trigger');

  // /editor?workflow=<id> opens a saved workflow from the server.
  useEffect(() => {
    const id = params.get('workflow');
    if (id) {
      // Always re-read: the server copy (graph, schedule state) may have changed since the draft was saved.
      void loadWorkflow(id);
      setParams({}, { replace: true });
    }
  }, [params, loadWorkflow, setParams]);

  const [chatOpen, setChatOpen] = useState(false);
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState<{ role: 'user' | 'assistant'; text: string }[]>([
    { role: 'assistant', text: 'Привет! Я помогу настроить ноды. Подключение к LLM появится на следующем этапе.' },
  ]);

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ name, repository_url: project, graph: toApiGraph(nodes, edges, types) }, null, 2)], { type: 'application/json' });
    download(blob, 'cyberflow-workflow.json');
  };

  const exportCi = async () => {
    setDownloadingCi(true);
    try {
      const id = await save();
      if (id) download(await getSecurityWorkflow(id), 'security.yml');
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) });
    } finally {
      setDownloadingCi(false);
    }
  };

  async function saveAsTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (templateName === null) return;
    setSavingTemplate(true);
    try {
      const id = await save();
      if (id === null) return;
      const template = await createTemplate({ workflow_id: id, name: templateName.trim(), description: templateDescription.trim() });
      setTemplateName(null);
      setNotice({ kind: 'success', text: `Шаблон «${template.name}» сохранён.` });
    } catch (failure) {
      setNotice({ kind: 'error', text: errorMessage(failure) });
    } finally {
      setSavingTemplate(false);
    }
  }

  const send = () => {
    if (!message.trim()) return;
    setMessages((ms) => [
      ...ms,
      { role: 'user', text: message.trim() },
      { role: 'assistant', text: 'Сейчас это демонстрационный чат. После подключения LLM я смогу предлагать изменения параметров и схемы.' },
    ]);
    setMessage('');
  };

  return (
    <div className="editor-page">
      <header className="editor-toolbar">
        <div>
          <h1>Редактор сценария</h1>
          <span className="muted">{name} · {workflowId ? 'сохранён на сервере' : 'не сохранён'}</span>
        </div>
        <div className="toolbar-actions">
          <button className="btn secondary" onClick={() => newWorkflow()} disabled={!!busy} title="Новый сценарий">
            <FilePlus size={16} /> Новый
          </button>
          <button className="btn secondary" onClick={() => void save()} disabled={!!busy}>
            <Save size={16} /> {busy === 'save' ? 'Сохранение…' : 'Сохранить'}
          </button>
          <button className="btn secondary" disabled={!!busy || savingTemplate} onClick={() => {
            setTemplateName(name);
            setTemplateDescription('');
          }}><BookMarked size={16} /> Сохранить как шаблон</button>
          <button className="btn secondary" onClick={exportJson}>
            <Download size={16} /> JSON
          </button>
          {hasCiTrigger && (
            <button className="btn secondary" disabled={!!busy || downloadingCi} onClick={() => void exportCi()}>
              <Download size={16} /> security.yml
            </button>
          )}
          {!hasCiTrigger && <button className="btn primary" disabled={!!busy} onClick={() => void runWorkflow()}>
            <Play size={16} /> {busy === 'run' ? 'Выполнение…' : 'Запустить'}
          </button>}
        </div>
      </header>

      {notice && (
        <div className={`notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
          <span>{notice.text}</span>
          {run && !busy && run.status !== 'failed' && <Link to={`/results?run=${run.id}`}>Открыть результаты</Link>}
          <button className="icon-btn" aria-label="Скрыть" onClick={() => setNotice(null)}><X size={15} /></button>
        </div>
      )}

      {templateName !== null && (
        <form className="panel account-form editor-template" onSubmit={event => void saveAsTemplate(event)}>
          <h2>Сохранить как шаблон</h2>
          <div className="account-fields">
            <label>Название<input value={templateName} maxLength={120} required onChange={event => setTemplateName(event.target.value)} /></label>
            <label>Описание<input value={templateDescription} maxLength={1000} onChange={event => setTemplateDescription(event.target.value)} /></label>
          </div>
          <div className="toolbar-actions">
            <button className="btn primary" disabled={savingTemplate || !!busy}>Сохранить шаблон</button>
            <button className="btn secondary" type="button" disabled={savingTemplate} onClick={() => setTemplateName(null)}>Отмена</button>
          </div>
        </form>
      )}

      {hasCiTrigger && <CiSetup key={project} />}

      <div className="editor-layout">
        <NodePalette />
        <Canvas />

        <div className={`editor-sidebar ${chatOpen ? 'chat-expanded' : ''}`}>
          <div className="editor-settings-area">
            <SettingsPanel />
          </div>

          <div className="editor-chat-area">
            <button
              type="button"
              className="chat-toggle"
              onClick={() => setChatOpen((v) => !v)}
              aria-expanded={chatOpen}
            >
              <Bot size={19} />
              <span>AI-помощник</span>
              {chatOpen ? <X size={17} /> : <MessageCircle size={17} />}
            </button>

            <div className="chat-messages">
              {messages.map((m, i) => (
                <div key={i} className={`chat-message ${m.role}`}>
                  {m.text}
                </div>
              ))}
            </div>

            <div className="chat-input">
              <input
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onFocus={() => setChatOpen(true)}
                onKeyDown={(e) => e.key === 'Enter' && send()}
                placeholder="Например: настрой проверку секретов…"
                aria-label="Сообщение AI-помощнику"
              />
              <button type="button" onClick={send} aria-label="Отправить">
                <Send size={17} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
