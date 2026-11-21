import { useState, type FormEvent } from 'react';
import { ChevronDown, Plus, X } from 'lucide-react';
import { errorMessage } from '../shared/api/client';
import { createWorkspace, type Workspace } from '../shared/api/workspaces';

export function WorkspaceSwitcher({ activeId, workspaces, onChange, onCreated, locked }: {
  activeId: string; workspaces: Workspace[]; onChange: (id: string) => void; onCreated: (workspace: Workspace) => void; locked: boolean;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !name.trim()) return;
    setBusy(true);
    setError('');
    try {
      onCreated(await createWorkspace(name.trim()));
      setName('');
      setCreating(false);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return <div className="workspace-switcher">
    <label htmlFor="active-workspace">Рабочее пространство</label>
    <div className="workspace-switcher-controls">
      <div className="workspace-select">
        <select id="active-workspace" value={activeId} onChange={event => onChange(event.target.value)} disabled={busy || locked}>
          {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
        </select>
        <ChevronDown size={16} aria-hidden="true" />
      </div>
      <button type="button" className="btn secondary"
        aria-expanded={creating} onClick={() => { setCreating(value => !value); setError(''); }} disabled={busy || locked}>
        {creating ? <X size={16} /> : <Plus size={16} />}
        {creating ? 'Отмена' : 'Новое пространство'}
      </button>
    </div>
    {creating && <form className="account-form workspace-create" onSubmit={event => void submit(event)}>
      <label>Название команды<input autoFocus value={name} required maxLength={120} onChange={event => setName(event.target.value)} /></label>
      <button className="btn primary" disabled={busy || locked || !name.trim()}>Создать</button>
      {error && <p className="notice error" role="alert">{error}</p>}
    </form>}
  </div>;
}
