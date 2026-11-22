import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useNodeTypesStore } from '../features/editor/nodeTypesStore';
import { ReactFlowProvider } from '@xyflow/react';
import { BookMarked, FolderGit2, Github, ShieldCheck, LayoutDashboard, Workflow, FileSearch, History as HistoryIcon, KeyRound, LogOut, Menu, X, UserRound, UsersRound, ScanLine, ArrowRight } from 'lucide-react';
import { Scenarios } from '../pages/Scenarios';
import { Results } from '../pages/Results';
import { History } from '../pages/History';
import { Credentials } from '../pages/Credentials';
import { Repositories } from '../pages/Repositories';
import { Templates } from '../pages/Templates';
import { TeamSettings } from '../pages/TeamSettings';
import { WorkspaceLogs } from '../pages/WorkspaceLogs';
import { JoinTeam, pendingInvitation, rememberInvitation } from '../pages/JoinTeam';
import { AccountProfile } from '../pages/Profile';
import { ApiError, apiUrl, errorMessage, SESSION_EXPIRED, WORKSPACE_UNAVAILABLE } from '../shared/api/client';
import { getProfile, logout, type Profile } from '../shared/api/account';
import { useEditorStore } from '../features/editor/store';
import { listWorkspaces, type Workspace } from '../shared/api/workspaces';
import { WorkspaceContext } from './workspaceContext';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { WorkspaceResource } from './WorkspaceResource';

const Editor = lazy(() => import('../features/editor/Editor').then(module => ({ default: module.Editor })));

const LOGIN_ERRORS: Record<string, string> = {
  cancelled: 'Вход через GitHub отменён.',
  expired: 'Срок запроса на вход истёк. Повторите вход.',
  github: 'GitHub не подтвердил вход. Попробуйте ещё раз.',
};

function savedWorkspace(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function rememberWorkspace(key: string, id: string) {
  try { localStorage.setItem(key, id); } catch { /* keep the selection in memory */ }
}

function Shell({ user, onProfileChanged }: { user: Profile; onProfileChanged: (profile: Profile) => void }) {
  const refreshCatalog = useNodeTypesStore(s => s.refresh);
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [logoutError, setLogoutError] = useState('');
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeId, setActiveId] = useState('');
  const activeWorkspaceId = useRef('');
  const pendingWorkspaceOperations = useRef(0);
  const [switchLocked, setSwitchLocked] = useState(false);
  const [workspaceError, setWorkspaceError] = useState('');
  const [workspaceRefresh, setWorkspaceRefresh] = useState(0);
  const storageKey = `cyberflow:workspace:${user.id}`;
  const workspace = workspaces.find(item => item.id === activeId);
  useEffect(() => { void refreshCatalog(); }, [refreshCatalog]);

  useEffect(() => {
    const refresh = () => setWorkspaceRefresh(value => value + 1);
    window.addEventListener(WORKSPACE_UNAVAILABLE, refresh);
    return () => window.removeEventListener(WORKSPACE_UNAVAILABLE, refresh);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setWorkspaceError('');
    void listWorkspaces(controller.signal).then(items => {
      if (controller.signal.aborted) return;
      const savedId = activeWorkspaceId.current || new URLSearchParams(window.location.search).get('workspace') || savedWorkspace(storageKey);
      const selected = items.find(item => item.id === savedId) ?? items.find(item => item.personal);
      if (!selected) throw new Error('Нет доступного рабочего пространства.');
      useEditorStore.getState().setWorkspace(selected.id);
      activeWorkspaceId.current = selected.id;
      rememberWorkspace(storageKey, selected.id);
      setWorkspaces(items);
      setActiveId(selected.id);
    }).catch(error => {
      if (!controller.signal.aborted) setWorkspaceError(errorMessage(error));
    });
    return () => controller.abort();
  }, [storageKey, workspaceRefresh]);

  function selectWorkspace(id: string, keepLink = false) {
    if (id === activeId || pendingWorkspaceOperations.current) return;
    const editor = useEditorStore.getState();
    if (editor.hasUnsavedChanges() && !window.confirm('Отбросить несохранённые изменения и сменить рабочее пространство?')) return;
    editor.setWorkspace(id);
    activeWorkspaceId.current = id;
    rememberWorkspace(storageKey, id);
    setActiveId(id);
    if (!keepLink) navigate(location.pathname === '/editor' ? '/' : location.pathname, { replace: true });
  }

  async function withWorkspaceLock<T>(operation: () => Promise<T>) {
    pendingWorkspaceOperations.current++;
    setSwitchLocked(true);
    try {
      return await operation();
    } finally {
      if (--pendingWorkspaceOperations.current === 0) setSwitchLocked(false);
    }
  }

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
      <nav className="app-nav" aria-label="Основная навигация" data-open={menuOpen}>
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={21} />
          </div>
          CyberFlow
        </div>
        <button type="button" className="menu-button" aria-controls="app-links" aria-expanded={menuOpen}
          onClick={() => setMenuOpen(open => !open)}>{menuOpen ? <X size={18} /> : <Menu size={18} />}Меню</button>
        <div className="nav-links" id="app-links" onClick={event => {
          if ((event.target as Element).closest('a')) setMenuOpen(false);
        }}>
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
          {workspace?.role === 'owner' && <NavLink to="/credentials">
            <KeyRound size={18} /> Учётные данные
          </NavLink>}
          <NavLink to="/results">
            <FileSearch size={18} /> Результаты
          </NavLink>
          <NavLink to="/history">
            <HistoryIcon size={18} /> История
          </NavLink>
          <NavLink to="/settings/logs"><FileSearch size={18} /> Журналы</NavLink>
          <NavLink to="/profile">
            <UserRound size={18} /> Профиль
          </NavLink>
          {workspace && !workspace.personal && workspace.role === 'owner' &&
            <NavLink to="/team"><UsersRound size={18} /> Настройки команды</NavLink>}
        </div>
        <div className="nav-footer account-nav-footer">
          <div className="account-identity"><span className="account-avatar" aria-hidden="true">{user.github_login.slice(0, 1).toUpperCase()}</span><span>@{user.github_login}</span></div>
          {logoutError && <p role="alert">{logoutError}</p>}
          <button className="btn secondary" onClick={() => void signOut()} disabled={leaving}>
            <LogOut size={15} /> {leaving ? 'Выход…' : 'Выйти'}
          </button>
        </div>
      </nav>
      <div className="main-content">
        {workspace && <div className="workspace-bar">
          <WorkspaceSwitcher activeId={activeId} workspaces={workspaces} onChange={selectWorkspace} locked={switchLocked}
            onCreated={item => {
              setWorkspaces(items => [...items, item]);
              selectWorkspace(item.id);
            }} />
          {workspace.role === 'member' && <span className="muted">Только просмотр</span>}
        </div>}
        {workspaceError && <div className="page"><p className="notice error" role="alert">{workspaceError}</p>
          <button className="btn secondary" onClick={() => setWorkspaceRefresh(value => value + 1)}>Повторить</button></div>}
        {!workspace && !workspaceError && <p className="page" role="status">Загружаем рабочие пространства…</p>}
        {workspace && <WorkspaceContext.Provider key={workspace.id} value={{ workspace, workspaces, switchLocked, withWorkspaceLock }}><Routes>
          <Route path="/" element={<Repositories />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/profile" element={<AccountProfile profile={user} onChange={onProfileChanged} />} />
          <Route path="/team" element={!workspace.personal && workspace.role === 'owner'
            ? <TeamSettings userId={user.id} /> : <Navigate to="/" replace />} />
          <Route path="/editor" element={<WorkspaceResource param="workflow" onSwitch={id => selectWorkspace(id, true)}>
            <Suspense fallback={<p className="page" role="status">Загружаем редактор…</p>}>
              <ReactFlowProvider><Editor /></ReactFlowProvider>
            </Suspense>
          </WorkspaceResource>} />
          <Route path="/results" element={<WorkspaceResource param="run" onSwitch={id => selectWorkspace(id, true)}>
            <Results />
          </WorkspaceResource>} />
          <Route path="/history" element={<History />} />
          <Route path="/settings/logs" element={<WorkspaceLogs key={workspace.id} />} />
          <Route path="/credentials" element={<WorkspaceResource param="project" onSwitch={id => selectWorkspace(id, true)}>
            {workspace.role === 'owner' ? <Credentials /> : <Navigate to="/" replace />}
          </WorkspaceResource>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes></WorkspaceContext.Provider>}
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

  const teamId = /^\/join\/([0-9a-f-]{36})$/.exec(location.pathname)?.[1];
  if (user && teamId) return <JoinTeam teamId={teamId} user={user} />;
  if (user) return <Shell user={user} onProfileChanged={storeProfile} />;

  const query = new URLSearchParams(location.search);
  const loginError = LOGIN_ERRORS[query.get('auth_error') ?? ''];
  query.delete('auth_error');
  const retryTeam = loginError && pendingInvitation()?.teamId;
  const next = retryTeam ? '/join/' + retryTeam : location.pathname + (query.size ? '?' + query : '');
  const loginUrl = apiUrl('/api/auth/github/start?' + new URLSearchParams({ next }));

  return (
    <main className="login-screen sign-in">
      <div className="sign-in-layout">
        <div className="sign-in-visual">
          <div className="brand"><span className="brand-mark"><ShieldCheck size={23} /></span>CyberFlow</div>
          <div className="sign-in-flow">
            <h2>Как устроена проверка</h2>
            <ol>
              <li><FolderGit2 /><div><strong>Репозиторий</strong><small>Код из GitHub</small></div></li>
              <li><ScanLine /><div><strong>Сценарий</strong><small>Набор проверок</small></div></li>
              <li><FileSearch /><div><strong>Отчёт</strong><small>Результаты запуска</small></div></li>
            </ol>
          </div>
        </div>
        <section className="login-card" aria-labelledby="login-title">
          <h1 id="login-title">Вход в CyberFlow</h1>
          <p className="muted">Войдите с аккаунтом GitHub, чтобы открыть свои репозитории, сценарии и результаты проверок.</p>
          {loginError && <p className="notice error" role="alert">{loginError}</p>}
          {user === undefined ? <p role="status">Проверяем сессию…</p> : error ? (
            <>
              <p className="notice error" role="alert">{error}</p>
              <button className="btn secondary" onClick={() => { setUser(undefined); setError(''); setAttempt(value => value + 1); }}>
                Повторить
              </button>
            </>
          ) : (
            <a className="btn primary" href={loginUrl} onClick={event => {
              if (teamId && location.hash) {
                try { rememberInvitation(teamId, location.hash); }
                catch {
                  event.preventDefault();
                  setError('Разрешите хранилище вкладки для возврата к приглашению после входа через GitHub.');
                }
              }
            }}><Github size={19} /> Войти через GitHub <ArrowRight size={17} /></a>
          )}
        </section>
      </div>
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
