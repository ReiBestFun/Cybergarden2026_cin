import { Trash2 } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';
import { SchemaForm } from './SchemaForm';
import { ConditionSettings } from './ConditionSettings';
import { ScheduleSettings } from './ScheduleSettings';
import { CredentialPicker } from './CredentialPicker';
import { NodeInspector } from './NodeInspector';

// Nodes whose settings are edited by a dedicated panel instead of the schema form.
const CUSTOM_SETTINGS = new Set(['schedule_trigger']);

export function SettingsPanel() {
  const { nodes, selected, project, setProject, name, setName, updateConfig, setConditions, renameSelected, removeSelected, run } = useEditorStore();
  const selectedNode = nodes.find(n => n.id === selected);
  const definition = useNodeTypesStore(s => s.types.find(t => t.kind === selectedNode?.data.kind));
  const nodeRun = run?.nodes.find(n => n.node_id === selected);
  const credentialKind = selectedNode?.data.kind === 'notify_telegram' ? 'telegram'
    : selectedNode?.data.kind === 'notify_email' ? 'smtp' : null;

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

          {definition && selectedNode.data.kind === 'condition' &&
            <ConditionSettings schema={definition.params_schema} data={selectedNode.data} onChange={setConditions} />}
          {definition && selectedNode.data.kind !== 'condition' && (Object.keys(definition.params_schema.properties ?? {}).length > 0 || !CUSTOM_SETTINGS.has(selectedNode.data.kind)) && (
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
          {credentialKind && <CredentialPicker kind={credentialKind} value={selectedNode.data.config?.credential_id ?? ''} />}

          {nodeRun && <NodeInspector key={`${run?.id}:${nodeRun.node_id}`} node={nodeRun} />}
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
