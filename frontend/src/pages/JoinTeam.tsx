import { useState } from 'react';
import { api, errorMessage } from '../shared/api/client';
import type { Profile } from '../shared/api/account';

export function JoinTeam({ teamId, user }: { teamId: string; user: Profile }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function accept() {
    setBusy(true);
    try {
      const token = location.hash.slice(1) || sessionStorage.getItem('invite:' + teamId);
      if (!token) throw new Error('Откройте полную ссылку приглашения.');
      await api('/api/workspaces/' + teamId + '/join', { method: 'POST', json: { token } });
      sessionStorage.removeItem('invite:' + teamId);
      localStorage.setItem('cyberflow:workspace:' + user.id, teamId);
      location.replace('/');
    } catch (failure) { setError(errorMessage(failure)); setBusy(false); }
  }
  return <main className="login-screen"><section className="panel login-card"><h1>Приглашение в команду</h1>
    <p>Вы вошли через GitHub как @{user.github_login}.</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    <button className="btn primary" disabled={busy} onClick={() => void accept()}>Принять приглашение</button>
    <a href="/">На главную</a>
  </section></main>;
}
