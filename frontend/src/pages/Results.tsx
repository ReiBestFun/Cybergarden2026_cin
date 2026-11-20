import { useNavigate } from 'react-router-dom';
import { Plus, CheckCircle2, CircleAlert, Download } from 'lucide-react';
export function Results() {
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

