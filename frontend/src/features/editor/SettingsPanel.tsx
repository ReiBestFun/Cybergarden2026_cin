import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Trash2 } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';
import { SchemaForm } from './SchemaForm';
import { ScheduleSettings } from './ScheduleSettings';
import { CredentialPicker } from './CredentialPicker';
import { NodeInspector } from './NodeInspector';
import { listTelegramDestinations, type TelegramDestination } from '../../shared/api/account';
import { errorMessage } from '../../shared/api/client';

// Nodes whose settings are edited by a dedicated panel instead of the schema form.
const CUSTOM_SETTINGS = new Set(['schedule_trigger']);

export function SettingsPanel() {
  const { nodes, selected, project, setProject, name, setName, updateConfig, renameSelected, removeSelected, run } = useEditorStore();
  const selectedNode = nodes.find(n => n.id === selected);
  const definition = useNodeTypesStore(s => s.types.find(t => t.kind === selectedNode?.data.kind));
  const nodeRun = run?.nodes.find(n => n.node_id === selected);
  const credentialKind = selectedNode?.data.kind === 'notify_telegram' ? 'telegram'
    : selectedNode?.data.kind === 'notify_email' ? 'smtp' : null;
  const isTelegram = selectedNode?.data.kind === 'notify_telegram';
  let schema = definition?.params_schema;
  if (isTelegram && schema) {
    const { recipients: _recipients, ...properties } = schema.properties ?? {};
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

          {isTelegram && <TelegramRecipients value={selectedNode.data.config?.recipients ?? ''} onChange={value => updateConfig('recipients', value)} />}
          {schema && (Object.keys(schema.properties ?? {}).length > 0 || !CUSTOM_SETTINGS.has(selectedNode.data.kind)) && (
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

function TelegramRecipients({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [destinations, setDestinations] = useState<TelegramDestination[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    listTelegramDestinations().then(page => {
      if (active) setDestinations(page.items.filter(chat => chat.verified_at));
    }, failure => {
      if (active) setError(errorMessage(failure));
    });
    return () => { active = false; };
  }, []);

  const selected = new Set(value.split('\n').map(chat => chat.trim()).filter(Boolean));
  const verified = new Set(destinations?.map(chat => chat.chat_id));
  const hasUnverified = destinations && [...selected].some(chat => !verified.has(chat));
  return (
    <fieldset className="telegram-picker">
      <legend>Получатели Telegram</legend>
      {error && <small role="alert">{error}</small>}
      {!destinations && !error && <small className="muted">Загрузка чатов…</small>}
      {destinations?.length === 0 && <p className="muted">Добавьте и подтвердите Telegram-чат в профиле.</p>}
      {destinations?.map(chat => (
        <label key={chat.id}>
          <input type="checkbox" checked={selected.has(chat.chat_id)} onChange={event => {
            if (event.target.checked) selected.add(chat.chat_id);
            else selected.delete(chat.chat_id);
            onChange([...selected].join('\n'));
          }} />
          {chat.label}
        </label>
      ))}
      {hasUnverified && (
        <button className="btn secondary" onClick={() => onChange([...selected].filter(chat => verified.has(chat)).join('\n'))}>
          Убрать неподтверждённые чаты
        </button>
      )}
      <Link to="/profile">Управлять чатами</Link>
    </fieldset>
  );
}
