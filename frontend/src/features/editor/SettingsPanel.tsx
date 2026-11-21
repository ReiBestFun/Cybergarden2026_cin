import { Trash2 } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';
import { SchemaForm } from './SchemaForm';
import { ScheduleSettings } from './ScheduleSettings';

// Nodes whose settings are edited by a dedicated panel instead of the schema form.
const CUSTOM_SETTINGS = new Set(['schedule_trigger']);

export function SettingsPanel() {
  const { nodes, selected, project, setProject, name, setName, updateConfig, renameSelected, removeSelected, run } = useEditorStore();
  const selectedNode = nodes.find(n => n.id === selected);
  const definition = useNodeTypesStore(s => s.types.find(t => t.kind === selectedNode?.data.kind));
  const nodeRun = run?.nodes.find(n => n.node_id === selected);

  return (
    <aside className="settings-panel">
      <h3>Настройки ноды</h3>
      {selectedNode ? (
        <>
          <div className="selected-heading">
            <strong>{selectedNode.data.label}</strong>
            <button className="icon-btn" title="Удалить ноду" onClick={removeSelected}>
              <Trash2 size={17} />
            </button>
          </div>

          <label>
            Название
            <input value={selectedNode.data.label} onChange={(e) => renameSelected(e.target.value)} />
          </label>

          <label>
            Тип ноды
            <input readOnly value={selectedNode.data.kind} />
          </label>

          {definition && (Object.keys(definition.params_schema.properties ?? {}).length > 0 || !CUSTOM_SETTINGS.has(selectedNode.data.kind)) && (
            <SchemaForm schema={definition.params_schema} values={selectedNode.data.config ?? {}} onChange={updateConfig} />
          )}
          {selectedNode.data.kind === 'repository' && (
            <label>
              URL проверяемого репозитория
              <input
                type="url"
                value={project}
                onChange={(e) => setProject(e.target.value)}
                placeholder="https://github.com/owner/repository"
              />
              <small className="muted">
                Репозиторий привязывается к проекту при сохранении сценария.
              </small>
            </label>
          )}

          {selectedNode.data.kind === 'schedule_trigger' && <ScheduleSettings />}

          {nodeRun && (
            <div className="node-run">
              <h4>Последний запуск: {nodeRun.status}</h4>
              {nodeRun.error && <p className="notice error">{nodeRun.error.message}</p>}
              {nodeRun.output_preview && <pre>{JSON.stringify(nodeRun.output_preview, null, 2)}</pre>}
            </div>
          )}
        </>
      ) : (
        <>
          <p className="muted">Выберите ноду на рабочем поле, чтобы изменить её параметры.</p>
          <label>
            Название сценария
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Учебный репозиторий
            <input value={project} onChange={(e) => setProject(e.target.value)} />
          </label>
        </>
      )}
    </aside>
  );
}
