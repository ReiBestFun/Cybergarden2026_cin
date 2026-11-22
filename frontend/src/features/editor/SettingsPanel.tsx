import { FilePicker } from '../../shared/components/FilePicker';
import { TelegramRecipients } from '../../shared/components/TelegramRecipients';
import { Link } from 'react-router-dom';
import { Trash2 } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';
import { SchemaForm } from './SchemaForm';
import { ConditionSettings } from './ConditionSettings';
import { ScheduleSettings } from './ScheduleSettings';
import { CredentialPicker } from './CredentialPicker';
import { NodeInspector } from './NodeInspector';

import { useWorkspace } from '../../app/workspaceContext';

// Nodes whose settings are edited by a dedicated panel instead of the schema form.
const CUSTOM_SETTINGS = new Set(['schedule_trigger', 'repository']);

export function SettingsPanel() {
  const { workspace, withWorkspaceLock } = useWorkspace();
  const uploadArchive = useEditorStore(s => s.uploadArchive);
  const setNodes = useEditorStore(s => s.setNodes);
  const busy = useEditorStore(s => s.busy);
  const { nodes, selected, project, setProject, name, setName, updateConfig, setConditions, renameSelected, removeSelected, run } = useEditorStore();
  const selectedNode = nodes.find(n => n.id === selected);
  const definition = useNodeTypesStore(s => s.types.find(t => t.kind === selectedNode?.data.kind));
  const nodeRun = run?.nodes.find(n => n.node_id === selected);
  const credentialKind = selectedNode?.data.kind === 'notify_telegram' ? 'telegram'
    : selectedNode?.data.kind === 'notify_email' ? 'smtp' : null;
  const isTelegram = selectedNode?.data.kind === 'notify_telegram';
  const usesWorkspace = selectedNode?.data.config?.use_workspace_settings === 'true';
  let schema = definition?.params_schema;
  if (credentialKind && schema) {
    const properties = { ...schema.properties };
    if (isTelegram || usesWorkspace) delete properties.recipients;
    if (isTelegram && selectedNode?.data.config?.summary_only === 'true') delete properties.attach_json;
    if (workspace.personal && !usesWorkspace) delete properties.use_workspace_settings;
    schema = { ...schema, properties };
  }

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
          {isTelegram && !usesWorkspace && <TelegramRecipients value={selectedNode.data.config?.recipients ?? ''} onChange={value => updateConfig('recipients', value)} />}
          {selectedNode.data.kind === 'notify_slack' && <p className="muted">{workspace.personal
            ? 'Для Slack нужно командное рабочее пространство.'
            : <Link to="/team">Настроить канал Slack команды</Link>}</p>}
          {usesWorkspace && <p className="muted">{workspace.personal
            ? 'Командные настройки доступны в командном пространстве. Отключите этот режим, чтобы задать получателей в ноде.'
            : <Link to="/team">Изменить получателей и реквизиты команды</Link>}</p>}
          {selectedNode.data.kind === 'local_archive' && <label className="archive-picker">
            ZIP с исходниками
            <FilePicker buttonText={selectedNode.data.config?.archive_sha256 ? 'Заменить ZIP' : 'Выбрать ZIP'}
              accept=".zip,application/zip" disabled={!!busy} onChange={event => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void withWorkspaceLock(() => uploadArchive(selectedNode.id, file));
              }} />
            <small className="muted" role="status">{busy === 'upload' ? 'Загружаем ZIP…' : selectedNode.data.config?.archive_sha256
              ? `Подключён ZIP · ${selectedNode.data.config.filename ?? 'Исходники'} · SHA-256 ${selectedNode.data.config.archive_sha256.slice(0, 12)}` : 'До 8 МиБ. Выберите ZIP с исходниками проекта.'}</small>
          </label>}
          {schema && selectedNode.data.kind !== 'local_archive' && selectedNode.data.kind !== 'condition' && (Object.keys(schema.properties ?? {}).length > 0 || !CUSTOM_SETTINGS.has(selectedNode.data.kind)) && (
            <SchemaForm schema={schema} values={selectedNode.data.config ?? {}} onChange={updateConfig} />
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

          {['repository', 'local_archive'].includes(selectedNode.data.kind) && !nodes.some(node => node.data.kind === 'ci_trigger') && (
            <button className="btn secondary" disabled={!!busy} onClick={() => {
              const archive = selectedNode.data.kind === 'repository';
              setNodes(current => current.map(node => node.id === selectedNode.id ? { ...node, data: {
                ...node.data, kind: archive ? 'local_archive' : 'repository', label: archive ? 'Локальный ZIP' : 'Репозиторий', config: {},
              } } : node));
            }}>{selectedNode.data.kind === 'repository' ? 'Использовать локальный ZIP' : 'Использовать репозиторий'}</button>
          )}
          {selectedNode.data.kind === 'schedule_trigger' && <ScheduleSettings />}
          {credentialKind && !usesWorkspace && <CredentialPicker kind={credentialKind} value={selectedNode.data.config?.credential_id ?? ''} />}

          {nodeRun && <NodeInspector key={`${run?.id}:${nodeRun.node_id}`} node={nodeRun} />}
        </>
      ) : (
        <>
          <p className="muted">Выберите ноду на рабочем поле, чтобы изменить её параметры.</p>
          <label>
            Название сценария
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          {!nodes.some(node => node.data.kind === 'local_archive') && <label>
            Учебный репозиторий
            <input value={project} onChange={(e) => setProject(e.target.value)} />
          </label>}
        </>
      )}
    </aside>
  );
}
