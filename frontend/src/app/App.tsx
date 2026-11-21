import { useEffect } from 'react';
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { ReactFlowProvider } from '@xyflow/react';
import { ShieldCheck, LayoutDashboard, Workflow, FileSearch, History as HistoryIcon, KeyRound, Library } from 'lucide-react';
import { Editor } from '../features/editor/Editor';
import { Scenarios } from '../pages/Scenarios';
import { Results } from '../pages/Results';
import { History } from '../pages/History';
import { Credentials } from '../pages/Credentials';
import { Templates } from '../pages/Templates';
function Shell() {
  const refreshCatalog = useNodeTypesStore(s => s.refresh);
  useEffect(() => { void refreshCatalog(); }, [refreshCatalog]);
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
          <NavLink to="/templates">
            <Library size={18} /> Шаблоны
          </NavLink>
          <NavLink to="/results">
            <FileSearch size={18} /> Результаты
          </NavLink>
          <NavLink to="/history">
            <HistoryIcon size={18} /> История
          </NavLink>
          <NavLink to="/credentials">
            <KeyRound size={18} /> Учётные данные
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
          <Route path="/" element={<Scenarios />} />
          <Route path="/editor" element={<ReactFlowProvider><Editor /></ReactFlowProvider>} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/results" element={<Results />} />
          <Route path="/history" element={<History />} />
          <Route path="/credentials" element={<Credentials />} />
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
