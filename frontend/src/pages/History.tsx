import { useNavigate } from 'react-router-dom';
import { Plus, CheckCircle2, CircleAlert, Download } from 'lucide-react';
export function History() {
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

