import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Save, Download, Play, Bot, X, MessageCircle, Send, FilePlus } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';
import { NodePalette } from './NodePalette';
import { Canvas } from './Canvas';
import { SettingsPanel } from './SettingsPanel';
import { toApiGraph } from './graph';

export function Editor() {
  const { nodes, edges, project, name, workflowId, busy, notice, run, save, runWorkflow, loadWorkflow, newWorkflow, setNotice } = useEditorStore();
  const types = useNodeTypesStore(s => s.types);
  const [params, setParams] = useSearchParams();

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
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'cyberflow-workflow.json';
    a.click();
    URL.revokeObjectURL(a.href);
  };

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
          <button className="btn secondary" onClick={newWorkflow} disabled={!!busy} title="Новый сценарий">
            <FilePlus size={16} /> Новый
          </button>
          <button className="btn secondary" onClick={() => void save()} disabled={!!busy}>
            <Save size={16} /> {busy === 'save' ? 'Сохранение…' : 'Сохранить'}
          </button>
          <button className="btn secondary" onClick={exportJson}>
            <Download size={16} /> JSON
          </button>
          <button className="btn primary" disabled={!!busy} onClick={() => void runWorkflow()}>
            <Play size={16} /> {busy === 'run' ? 'Выполнение…' : 'Запустить'}
          </button>
        </div>
      </header>

      {notice && (
        <div className={`notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
          <span>{notice.text}</span>
          {run && !busy && run.status !== 'failed' && <Link to={`/results?run=${run.id}`}>Открыть результаты</Link>}
          <button className="icon-btn" aria-label="Скрыть" onClick={() => setNotice(null)}><X size={15} /></button>
        </div>
      )}

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
