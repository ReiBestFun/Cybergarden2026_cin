import { useEffect, useState } from 'react';
import { useWorkspace } from '../app/workspaceContext';
import { api, errorMessage } from '../shared/api/client';
import type { Workspace } from '../shared/api/workspaces';
import { WorkspaceDeliverySettings } from './WorkspaceDeliverySettings';

type Role = Workspace['role'];
type Member = { id: string; github_login: string; role: Role };
type Invitation = { id: string; role: Role; expires_at: string };
const roleNames = { owner: 'Владелец', member: 'Участник' };
const options = <><option value="member">Участник</option><option value="owner">Владелец</option></>;

export function TeamSettings({ userId }: { userId: string }) {
  const { workspace, withWorkspaceLock } = useWorkspace();
  const path = '/api/workspaces/' + workspace.id;
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [role, setRole] = useState<Role>('member');
  const [days, setDays] = useState(7);
  const [issued, setIssued] = useState<{ id: string; url: string }>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const init = { signal: controller.signal };
    Promise.all([api<Member[]>(path + '/members', init), api<Invitation[]>(path + '/invitations', init)])
      .then(([people, links]) => { if (!controller.signal.aborted) { setMembers(people); setInvitations(links); } })
      .catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [path, revision]);

  async function change(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try { await withWorkspaceLock(action); setRevision(value => value + 1); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  return <main className="page team-settings"><header><h1>Настройки команды</h1><span>{workspace.name}</span></header>
    {error && <p role="alert" className="notice error">{error}</p>}
    <section className="panel"><h2>Приглашения</h2>
      <form className="account-form team-invite-form" onSubmit={event => {
        event.preventDefault();
        void change(async () => {
          const link = await api<Invitation & { token: string }>(path + '/invitations', { method: 'POST', json: { role, days } });
          setIssued({ id: link.id, url: location.origin + '/join/' + workspace.id + '#' + link.token });
        });
      }}>
        <label>Роль<select value={role} onChange={e => setRole(e.target.value as Role)}>{options}</select></label>
        <label>Срок в днях<input type="number" min={1} max={30} required value={days || ''} onChange={e => setDays(Number(e.target.value))} /></label>
        <button className="btn primary" disabled={busy}>Создать приглашение</button>
      </form>
      {issued && <div className="account-form"><label>Скопируйте одноразовую ссылку для коллеги<input readOnly value={issued.url} onFocus={e => e.target.select()} /></label></div>}
      {invitations.map(link => <div className="team-row" key={link.id}>
        <span>{roleNames[link.role]} · до {new Date(link.expires_at).toLocaleString('ru-RU')}</span>
        <button className="btn secondary" disabled={busy} onClick={() => void change(async () => {
          await api(path + '/invitations/' + link.id, { method: 'DELETE' });
          if (issued?.id === link.id) setIssued(undefined);
        })}>Отозвать</button>
      </div>)}
    </section>
    <section className="panel"><h2>Участники</h2>
      {members.map(member => <div className="team-row" key={member.id}>
        <span>@{member.github_login}</span>
        <div className="toolbar-actions">
          <select aria-label={'Роль @' + member.github_login} value={member.role} disabled={busy} onChange={event => {
            const role = event.target.value;
            void change(async () => {
              await api(path + '/members/' + member.id, { method: 'PATCH', json: { role } });
              if (member.id === userId) location.replace('/');
            });
          }}>{options}</select>
          <button className="btn secondary" disabled={busy} onClick={() => {
            if (window.confirm('Удалить @' + member.github_login + ' из команды?')) void change(async () => {
              await api(path + '/members/' + member.id, { method: 'DELETE' });
              if (member.id === userId) location.replace('/');
            });
          }}>Удалить</button>
        </div>
      </div>)}
    </section>
    <WorkspaceDeliverySettings />
  </main>;
}
