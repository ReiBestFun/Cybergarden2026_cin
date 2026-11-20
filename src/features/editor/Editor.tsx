import { useState } from 'react';
import { Save, Download, Play, Bot, X, MessageCircle, Send } from 'lucide-react';
import { useEditorStore } from './store';
import { NodePalette } from './NodePalette';
import { Canvas } from './Canvas';
import { SettingsPanel } from './SettingsPanel';
export function Editor() {
  const { nodes, edges, project, setNodes } = useEditorStore();
  
  const [chatOpen, setChatOpen] = useState(true);
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState<{ role: 'user' | 'assistant'; text: string }[]>([
    { role: 'assistant', text: 'Привет! Я помогу настроить ноды. Подключение к LLM появится после реализации backend.' },
  ]);
  
  const [running, setRunning] = useState(false);

  const save = () => {
    localStorage.setItem('cyberflow.workflow', JSON.stringify({ nodes, edges, project }));
    alert('Сценарий сохранён в браузере');
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ nodes, edges, project }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'cyberflow-workflow.json';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const demoRun = async () => {
    if (running) return;
    setRunning(true);
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, status: undefined } })));
    
    const ids = nodes.map((n) => n.id);
    for (const id of ids) {
      setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, status: 'running' } } : n)));
      await new Promise((r) => setTimeout(r, 500));
      setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, status: 'done' } } : n)));
    }
    setRunning(false);
    localStorage.setItem('cyberflow.lastRun', new Date().toISOString());
  };

  const send = () => {
    if (!message.trim()) return;
    setMessages((ms) => [
      ...ms,
      { role: 'user', text: message.trim() },
      {
        role: 'assistant',
        text: 'Сейчас это демонстрационный чат. После подключения LLM я смогу предлагать изменения параметров и схемы. Изменения будут применяться только после подтверждения.',
      },
    ]);
    setMessage('');
  };

  return (
    <div className="editor-page">
      {/* Верхняя панель */}
      <header className="editor-toolbar">
        <div>
          <h1>Редактор сценария</h1>
          <span className="muted">Security check workflow · Черновик</span>
        </div>
        <div className="toolbar-actions">
          <button className="btn secondary" onClick={save}>
            <Save size={16} /> Сохранить
          </button>
          <button className="btn secondary" onClick={exportJson}>
            <Download size={16} /> JSON
          </button>
          <button className="btn primary" disabled={running} onClick={demoRun}>
            <Play size={16} /> {running ? 'Выполнение…' : 'Демо-запуск'}
          </button>
        </div>
      </header>

      {/* Основная рабочая область (3 колонки по CSS) */}
      <div className="editor-layout">
        {/* Левая панель: Библиотека нод */}
        <NodePalette />

        {/* Центральная панель: Холст (Canvas) */}
        <Canvas />

        {/* Правая панель: Настройки (исправлена вложенность для соответствия CSS) */}
        <SettingsPanel />
      </div>

      {/* Виджет AI-чата (вынесен из aside, теперь он прямой потомок .editor-page, как требует CSS) */}
      <div className={`chat-widget ${chatOpen ? 'open' : ''}`}>
        <button className="chat-toggle" onClick={() => setChatOpen((v) => !v)}>
          <Bot size={19} /> AI-помощник {chatOpen ? <X size={17} /> : <MessageCircle size={17} />}
        </button>
        {chatOpen && (
          <>
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
                onKeyDown={(e) => e.key === 'Enter' && send()}
                placeholder="Например: настрой проверку секретов…"
              />
              <button onClick={send} aria-label="Отправить">
                <Send size={17} />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

