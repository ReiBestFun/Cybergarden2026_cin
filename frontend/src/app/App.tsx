import { useEffect, useState } from 'react';
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { ReactFlowProvider } from '@xyflow/react';
import { BookMarked, FolderGit2, Github, ShieldCheck, LayoutDashboard, Workflow, FileSearch, History as HistoryIcon, KeyRound, LogOut, UserRound } from 'lucide-react';
import { Editor } from '../features/editor/Editor';
import { Scenarios } from '../pages/Scenarios';
import { Results } from '../pages/Results';
import { History } from '../pages/History';
import { Credentials } from '../pages/Credentials';
import { Repositories } from '../pages/Repositories';
import { Templates } from '../pages/Templates';
import { AccountProfile } from '../pages/Profile';
import { ApiError, apiUrl, errorMessage, SESSION_EXPIRED } from '../shared/api/client';
import { getProfile, logout, type Profile } from '../shared/api/account';
import { useEditorStore } from '../features/editor/store';

const LOGIN_ERRORS: Record<string, string> = {
  cancelled: 'Вход через GitHub отменён.',
  expired: 'Срок запроса на вход истёк. Повторите вход.',
  github: 'GitHub не подтвердил вход. Попробуйте ещё раз.',
};

function Shell({ user, onProfileChanged }: { user: Profile; onProfileChanged: (profile: Profile) => void }) {
  const refreshCatalog = useNodeTypesStore(s => s.refresh);
  const [leaving, setLeaving] = useState(false);
  const [logoutError, setLogoutError] = useState('');
  useEffect(() => { void refreshCatalog(); }, [refreshCatalog]);

  async function signOut() {
    setLeaving(true);
    setLogoutError('');
    try {
      await logout();
      window.location.replace('/');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        window.location.replace('/');
      } else {
        setLogoutError(errorMessage(error));
        setLeaving(false);
      }
    }
  }

  return (
    <div className="app-shell">
      <nav className="app-nav">
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={21} />
          </div>
          CyberFlow
        </div>
        <div className="nav-links">
          <NavLink to="/" end>
            <FolderGit2 size={18} /> Репозитории
          </NavLink>
          <NavLink to="/scenarios">
            <LayoutDashboard size={18} /> Сценарии
          </NavLink>
          <NavLink to="/templates">
            <BookMarked size={18} /> Шаблоны
          </NavLink>
          <NavLink to="/editor">
            <Workflow size={18} /> Редактор
          </NavLink>
          <NavLink to="/credentials">
            <KeyRound size={18} /> Учётные данные
          </NavLink>
          <NavLink to="/results">
            <FileSearch size={18} /> Результаты
          </NavLink>
          <NavLink to="/history">
            <HistoryIcon size={18} /> История
          </NavLink>
          <NavLink to="/profile">
            <UserRound size={18} /> Профиль
          </NavLink>
        </div>
        <div className="nav-footer account-nav-footer">
          <span>@{user.github_login}</span>
          {logoutError && <p role="alert">{logoutError}</p>}
          <button className="btn secondary" onClick={() => void signOut()} disabled={leaving}>
            <LogOut size={15} /> {leaving ? 'Выход…' : 'Выйти'}
          </button>
        </div>
      </nav>
      <div className="main-content">
        <Routes>
          <Route path="/" element={<Repositories />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/profile" element={<AccountProfile profile={user} onChange={onProfileChanged} />} />
          <Route path="/editor" element={<ReactFlowProvider><Editor /></ReactFlowProvider>} />
          <Route path="/results" element={<Results />} />
          <Route path="/history" element={<History />} />
          <Route path="/credentials" element={<Credentials />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}

function SessionGate() {
  const location = useLocation();
  const [user, setUser] = useState<Profile | null>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  const storeProfile = (profile: Profile) => {
    useEditorStore.getState().setProfileTimezone(profile.timezone);
    setUser(profile);
  };

  useEffect(() => {
    let active = true;
    getProfile().then(profile => {
      if (active) storeProfile(profile);
    }, failure => {
      if (!active) return;
      if (!(failure instanceof ApiError && failure.status === 401)) setError(errorMessage(failure));
      setUser(null);
    });
    return () => { active = false; };
  }, [attempt]);

  useEffect(() => {
    const expire = () => window.location.reload();
    const checkRestoredSession = (event: PageTransitionEvent) => {
      if (event.persisted) expire();
    };
    window.addEventListener(SESSION_EXPIRED, expire);
    window.addEventListener('pageshow', checkRestoredSession);
    return () => {
      window.removeEventListener(SESSION_EXPIRED, expire);
      window.removeEventListener('pageshow', checkRestoredSession);
    };
  }, []);

  if (user) return <Shell user={user} onProfileChanged={storeProfile} />;

  const query = new URLSearchParams(location.search);
  const loginError = LOGIN_ERRORS[query.get('auth_error') ?? ''];
  query.delete('auth_error');
  const next = location.pathname + (query.size ? '?' + query : '');
  const loginUrl = apiUrl('/api/auth/github/start?' + new URLSearchParams({ next }));

  return (
    <main className="login-screen">
      <section className="panel login-card" aria-labelledby="login-title">
        <span className="brand-mark"><ShieldCheck size={28} /></span>
        <h1 id="login-title">CyberFlow</h1>
        <p className="muted">Проверяйте репозитории и сохраняйте сценарии в личном кабинете.</p>
        {loginError && <p className="notice error" role="alert">{loginError}</p>}
        {user === undefined ? <p role="status">Проверяем сессию…</p> : error ? (
          <>
            <p className="notice error" role="alert">{error}</p>
            <button className="btn secondary" onClick={() => { setUser(undefined); setError(''); setAttempt(value => value + 1); }}>
              Повторить
            </button>
          </>
        ) : (
          <a className="btn primary" href={loginUrl}><Github size={19} /> Войти через GitHub</a>
        )}
      </section>
    </main>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <SessionGate />
    </BrowserRouter>
  );
}
