import { useState } from 'react';
import { api, errorMessage } from '../shared/api/client';
import type { Profile } from '../shared/api/account';

const pendingKey = 'cyberflow:pending-invitation';

export function pendingInvitation() {
  try {
    const saved = sessionStorage.getItem(pendingKey) ?? '';
    const match = /^([0-9a-f-]{36})#([\w-]{43})$/.exec(saved);
    return match && { teamId: match[1], token: match[2] };
  } catch { return null; }
}

export function rememberInvitation(teamId: string, hash: string) {
  sessionStorage.setItem(pendingKey, teamId + hash);
}

export function JoinTeam({ teamId, user }: { teamId: string; user: Profile }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function accept() {
    setBusy(true);
    try {
      const pending = pendingInvitation();
      const token = location.hash.slice(1) || (pending?.teamId === teamId && pending.token);
      if (!token) throw new Error('Откройте полную ссылку приглашения.');
      const team = await api<{ id: string }>('/api/workspaces/' + teamId + '/join', { method: 'POST', json: { token } });
      try { sessionStorage.removeItem(pendingKey); } catch { /* redemption is already complete */ }
      location.replace('/?workspace=' + encodeURIComponent(team.id));
    } catch (failure) { setError(errorMessage(failure)); setBusy(false); }
  }
  return <main className="login-screen"><section className="panel login-card"><h1>Приглашение в команду</h1>
    <p>Вы вошли через GitHub как @{user.github_login}.</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    <button className="btn primary" disabled={busy} onClick={() => void accept()}>Принять приглашение</button>
    <a href="/">На главную</a>
  </section></main>;
}
