import { useCallback, useState } from 'react';
import { BrowserRouter, NavLink, Route, Routes, useNavigate } from 'react-router-dom';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  addEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  ShieldCheck,
  LayoutDashboard,
  Workflow,
  FileSearch,
  History,
  Plus,
  Play,
  Save,
  Download,
  Trash2,
  Send,
  Bot,
  X,
  MessageCircle,
  CheckCircle2,
  CircleAlert,
  Settings2,
} from 'lucide-react';

// ==========================================
// 1. ТИПЫ И КОНСТАНТЫ
// ==========================================

type NodeKind = 'manual' | 'schedule' | 'secrets' | 'sca' | 'sast' | 'ai' | 'condition' | 'telegram';

type FlowData = {
  label: string;
  kind: NodeKind;
  status?: string;
  config?: Record<string, string>;
};

type FlowNode = Node<FlowData>;

const catalog: { kind: NodeKind; label: string; description: string; category: string }[] = [
  { kind: 'manual', label: 'Ручной запуск', description: 'Запуск по кнопке', category: 'Запуск' },
  { kind: 'schedule', label: 'Расписание', description: 'Периодический запуск', category: 'Запуск' },
  { kind: 'secrets', label: 'Secrets Scanner', description: 'Поиск секретов', category: 'Безопасность' },
  { kind: 'sca', label: 'SCA Scanner', description: 'Проверка зависимостей', category: 'Безопасность' },
  { kind: 'sast', label: 'SAST Scanner', description: 'Анализ Kotlin-кода', category: 'Безопасность' },
  { kind: 'condition', label: 'IF / ELSE', description: 'Условное ветвление', category: 'Логика' },
  { kind: 'ai', label: 'AI Analysis', description: 'Объяснение находок', category: 'AI' },
  { kind: 'telegram', label: 'Telegram', description: 'Уведомление', category: 'Уведомления' },
];

const initialNodes: FlowNode[] = [
  { id: 'n1', type: 'flowNode', position: { x: 130, y: 100 }, data: { kind: 'manual', label: 'Ручной запуск' } },
  { id: 'n2', type: 'flowNode', position: { x: 420, y: 100 }, data: { kind: 'secrets', label: 'Secrets Scanner' } },
  { id: 'n3', type: 'flowNode', position: { x: 710, y: 100 }, data: { kind: 'ai', label: 'AI Analysis' } },
  { id: 'n4', type: 'flowNode', position: { x: 1000, y: 100 }, data: { kind: 'telegram', label: 'Telegram' } },
];

const initialEdges: Edge[] = [
  { id: 'e1', source: 'n1', target: 'n2' },
  { id: 'e2', source: 'n2', target: 'n3' },
  { id: 'e3', source: 'n3', target: 'n4' },
];

function getSaved() {
  try {
    const raw = localStorage.getItem('cyberflow.workflow');
    if (raw) {
      const p = JSON.parse(raw);
      if (Array.isArray(p.nodes) && Array.isArray(p.edges)) return p;
    }
  } catch {}
  return { nodes: initialNodes, edges: initialEdges };
}

// ==========================================
// 2. КАСТОМНАЯ НОДА (Custom Node)
// ==========================================

function CustomNode({ data, selected }: NodeProps<FlowNode>) {
  return (
    <div className={`flow-node ${selected ? 'selected ' : ''}${data.kind}`}>
      <Handle type="target" position={Position.Left} />
      <div className="node-icon">
        {data.kind === 'ai' ? (
          <Bot size={18} />
        ) : data.kind === 'condition' ? (
          <Settings2 size={18} />
        ) : (
          <ShieldCheck size={18} />
        )}
      </div>
      <div>
        <strong>{data.label}</strong>
        <small>{catalog.find((c) => c.kind === data.kind)?.description}</small>
      </div>
      {data.status && (
        <span className={`node-status ${data.status}`}>
          {data.status === 'done' ? '✓' : data.status === 'running' ? '…' : '!'}
        </span>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { flowNode: CustomNode };

// ==========================================
// 3. СТРАНИЦА РЕДАКТОРА (Editor)
// ==========================================

function Editor() {
  const saved = getSaved();
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>(saved.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(saved.edges);
  const [selected, setSelected] = useState<string | null>(null);
  
  const [chatOpen, setChatOpen] = useState(true);
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState<{ role: 'user' | 'assistant'; text: string }[]>([
    { role: 'assistant', text: 'Привет! Я помогу настроить ноды. Подключение к LLM появится после реализации backend.' },
  ]);
  
  const [running, setRunning] = useState(false);
  const [project, setProject] = useState('https://github.com/TBagirov/training');

  const onConnect = useCallback(
    (connection: Connection) =>
      setEdges((eds) =>
        addEdge(
          { ...connection, animated: false, style: { stroke: '#a7a0b8', strokeWidth: 2 } },
          eds
        )
      ),
    [setEdges]
  );

  const addNode = useCallback(
    (kind: NodeKind, position?: { x: number; y: number }) => {
      const item = catalog.find((x) => x.kind === kind)!;
      const id = crypto.randomUUID();
      setNodes((ns) => [
        ...ns,
        {
          id,
          type: 'flowNode',
          position: position ?? { x: 160 + ns.length * 45, y: 180 + ns.length * 35 },
          data: { kind, label: item.label, config: {} },
        },
      ]);
      setSelected(id);
    },
    [setNodes]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const kind = e.dataTransfer.getData('application/cyberflow-node') as NodeKind;
      if (!catalog.some((c) => c.kind === kind)) return;
      const bounds = e.currentTarget.getBoundingClientRect();
      addNode(kind, { x: e.clientX - bounds.left - 110, y: e.clientY - bounds.top - 35 });
    },
    [addNode]
  );

  const selectedNode = nodes.find((n) => n.id === selected);

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

  const updateConfig = (key: string, value: string) =>
    setNodes((ns) =>
      ns.map((n) =>
        n.id === selected ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } } : n
      )
    );

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
        <aside className="node-library">
          <h3>Библиотека нод</h3>
          <p className="muted">Перетащите блок на поле</p>
          {[...new Set(catalog.map((c) => c.category))].map((cat) => (
            <section key={cat}>
              <h4>{cat}</h4>
              {catalog
                .filter((c) => c.category === cat)
                .map((item) => (
                  <div
                    key={item.kind}
                    draggable
                    onDragStart={(e) => e.dataTransfer.setData('application/cyberflow-node', item.kind)}
                    onClick={() => addNode(item.kind)}
                    className="library-node"
                  >
                    <Plus size={15} />
                    <span>
                      <strong>{item.label}</strong>
                      <small>{item.description}</small>
                    </span>
                  </div>
                ))}
            </section>
          ))}
        </aside>

        {/* Центральная панель: Холст (Canvas) */}
        <main className="canvas" onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => setSelected(n.id)}
            onPaneClick={() => setSelected(null)}
            fitView
          >
            <Background color="#dfd4c8" gap={22} size={1} />
            <Controls />
            <MiniMap pannable zoomable nodeColor="#cbbeb5" />
          </ReactFlow>
          <div className="canvas-hint">Демо: статусы имитируются. Реальные сканеры пока не подключены.</div>
        </main>

        {/* Правая панель: Настройки (исправлена вложенность для соответствия CSS) */}
        <aside className="settings-panel">
          <h3>Настройки ноды</h3>
          {selectedNode ? (
            <>
              <div className="selected-heading">
                <strong>{selectedNode.data.label}</strong>
                <button
                  className="icon-btn"
                  title="Удалить ноду"
                  onClick={() => {
                    setNodes((ns) => ns.filter((n) => n.id !== selected));
                    setEdges((es) => es.filter((e) => e.source !== selected && e.target !== selected));
                    setSelected(null);
                  }}
                >
                  <Trash2 size={17} />
                </button>
              </div>
              
              <label>
                Название
                <input
                  value={selectedNode.data.label}
                  onChange={(e) =>
                    setNodes((ns) =>
                      ns.map((n) => (n.id === selected ? { ...n, data: { ...n.data, label: e.target.value } } : n))
                    )
                  }
                />
              </label>
              
              <label>
                Тип ноды
                <input readOnly value={selectedNode.data.kind} />
              </label>

              {selectedNode.data.kind === 'schedule' && (
                <label>
                  Cron-выражение
                  <input
                    placeholder="0 14 * * *"
                    value={selectedNode.data.config?.cron ?? ''}
                    onChange={(e) => updateConfig('cron', e.target.value)}
                  />
                </label>
              )}

              {selectedNode.data.kind === 'condition' && (
                <label>
                  Условие
                  <input
                    placeholder="critical_count > 0"
                    value={selectedNode.data.config?.expression ?? ''}
                    onChange={(e) => updateConfig('expression', e.target.value)}
                  />
                </label>
              )}

              {selectedNode.data.kind === 'telegram' && (
                <label>
                  Chat ID
                  <input
                    placeholder="Telegram chat ID"
                    value={selectedNode.data.config?.chatId ?? ''}
                    onChange={(e) => updateConfig('chatId', e.target.value)}
                  />
                </label>
              )}

              {['secrets', 'sca', 'sast'].includes(selectedNode.data.kind) && (
                <label>
                  Путь внутри проекта
                  <input
                    placeholder="src/main"
                    value={selectedNode.data.config?.path ?? ''}
                    onChange={(e) => updateConfig('path', e.target.value)}
                  />
                </label>
              )}
              
              <p className="muted">
                Настройки сохраняются в графе. Секретные токены не вводите до подключения защищённого backend.
              </p>
            </>
          ) : (
            <>
              <p className="muted">Выберите ноду на рабочем поле, чтобы изменить её параметры.</p>
              <label>
                Учебный репозиторий
                <input value={project} onChange={(e) => setProject(e.target.value)} />
              </label>
            </>
          )}
        </aside>
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

// ==========================================
// 4. ДРУГИЕ СТРАНИЦЫ
// ==========================================

function Dashboard() {
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Мои сценарии</h1>
          <p className="muted">Создавайте и управляйте автоматическими проверками безопасности</p>
        </div>
        <button className="btn primary" onClick={() => nav('/editor')}>
          <Plus size={16} /> Открыть редактор
        </button>
      </div>
      <div className="summary-grid">
        <div className="panel">
          <span className="muted">Сценарии</span>
          <h2>1</h2>
        </div>
        <div className="panel">
          <span className="muted">Доступные анализаторы</span>
          <h2>3</h2>
        </div>
        <div className="panel">
          <span className="muted">Режим</span>
          <h2>Демо</h2>
        </div>
      </div>
      <div className="panel workflow-card" onClick={() => nav('/editor')}>
        <Workflow size={24} />
        <div>
          <strong>Security check workflow</strong>
          <p className="muted">Учебный проект · Kotlin / Spring Boot</p>
        </div>
        <span className="tag">Черновик</span>
      </div>
    </div>
  );
}

function Results() {
  const example = [
    { category: 'SECRET', title: 'Пример: секрет в конфигурации', severity: 'HIGH', path: 'src/main/resources/application.properties', line: '—' },
    { category: 'SCA', title: 'Пример: уязвимая зависимость', severity: 'HIGH', path: 'build.gradle.kts', line: '—' },
    { category: 'SAST', title: 'Пример: небезопасная настройка', severity: 'MEDIUM', path: 'src/main/kotlin/...', line: '—' },
  ];

  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Результаты проверок</h1>
          <p className="muted">Демонстрационные данные — не результаты сканирования репозитория</p>
        </div>
      </div>
      <div className="panel">
        <h3>Предварительный вид отчёта</h3>
        <table>
          <thead>
            <tr>
              <th>Категория</th>
              <th>Находка</th>
              <th>Критичность</th>
              <th>Путь</th>
              <th>Строка</th>
            </tr>
          </thead>
          <tbody>
            {example.map((x, i) => (
              <tr key={i}>
                <td>{x.category}</td>
                <td>{x.title}</td>
                <td>
                  {/* ИСПРАВЛЕНО: возвращён класс .tag из вашего CSS + добавлена inline-стилизация для наглядности */}
                  <span 
                    className="tag" 
                    style={{ 
                      background: x.severity === 'HIGH' ? '#fee2e2' : '#fef3c7', 
                      color: x.severity === 'HIGH' ? '#991b1b' : '#92400e' 
                    }}
                  >
                    {x.severity}
                  </span>
                </td>
                <td>
                  <code>{x.path}</code>
                </td>
                <td>{x.line}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          После подключения backend таблица и JSON-отчёт будут формироваться из единого массива findings по спецификации организаторов.
        </p>
      </div>
    </div>
  );
}

function Runs() {
  const last = localStorage.getItem('cyberflow.lastRun');
  return (
    <div className="page">
      <h1>История запусков</h1>
      <p className="muted">История реальных проверок появится после подключения backend.</p>
      <div className="panel">
        {last ? (
          <>
            <CheckCircle2 size={18} /> Последний демонстрационный запуск: {new Date(last).toLocaleString('ru-RU')}
          </>
        ) : (
          <>
            <CircleAlert size={18} /> Запусков пока нет.
          </>
        )}
      </div>
    </div>
  );
}

// ==========================================
// 5. ОСНОВНАЯ ОБЁРТКА И РОУТИНГ
// ==========================================

function Shell() {
  return (
    <div className="app-shell">
      <nav className="app-nav">
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={21} />
          </div>
          CyberFlow
        </div>
        <div className="nav-links">
          <NavLink to="/" end>
            <LayoutDashboard size={18} /> Сценарии
          </NavLink>
          <NavLink to="/editor">
            <Workflow size={18} /> Редактор
          </NavLink>
          <NavLink to="/results">
            <FileSearch size={18} /> Результаты
          </NavLink>
          <NavLink to="/history">
            <History size={18} /> История
          </NavLink>
        </div>
        <div className="nav-footer">
          Cyber Garden 2026
          <br />
          <span>Frontend MVP · v0.1</span>
        </div>
      </nav>
      <div className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/editor" element={<ReactFlowProvider><Editor /></ReactFlowProvider>} />
          <Route path="/results" element={<Results />} />
          <Route path="/history" element={<Runs />} />
        </Routes>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  );
}