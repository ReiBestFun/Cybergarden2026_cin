import { useNavigate } from 'react-router-dom';
import { Plus, CheckCircle2, CircleAlert, Download, Workflow } from 'lucide-react';
export function Scenarios() {
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

