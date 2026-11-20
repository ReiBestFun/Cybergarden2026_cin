import { Trash2 } from 'lucide-react';
import { useEditorStore } from './store';
export function SettingsPanel() {
 const {nodes,selected,project,setProject,updateConfig,renameSelected,removeSelected}=useEditorStore();
 const selectedNode=nodes.find(n=>n.id===selected);
 return (
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
                    removeSelected();
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
                    renameSelected(e.target.value)
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
 );
}
