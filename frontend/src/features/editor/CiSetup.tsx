import { useEffect, useRef, useState } from 'react';
import { Download, Github, Play } from 'lucide-react';
import { errorMessage } from '../../shared/api/client';
import { getSecurityWorkflow, issueCiToken } from '../../shared/api/workflows';
import { DEFAULT_REPOSITORY, TRAINING_CI_SHA } from './defaults';
import { useEditorStore } from './store';
import { useWorkspace } from '../../app/workspaceContext';

export function CiSetup({ onDownload }: { onDownload: (file: Blob, name: string) => void }) {
  const { withWorkspaceLock } = useWorkspace();
  const yamlRequest = useRef<AbortController>();
  const { project, nodes, workflowId, busy, save, runWorkflow } = useEditorStore();
  const repository = project.trim().replace(/\/$/, '').replace(/\.git$/, '');
  const githubUrl = /^https:\/\/github\.com\/[a-z\d-]+\/[a-z\d_.-]+$/i.test(repository) ? repository : null;
  const branch = nodes.find(node => node.data.kind === 'ci_trigger')?.data.config?.branch;
  const [commit, setCommit] = useState(repository.toLowerCase() === DEFAULT_REPOSITORY.toLowerCase() ? TRAINING_CI_SHA : '');
  const [issuedToken, setToken] = useState<{ id: string; value: string }>();
  const [generatedYaml, setYaml] = useState<{ id: string; branch?: string; value: string }>();
  const [runner, setRunner] = useState<'local' | 'hosted'>('local');
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState('');
  const sha = commit.trim().toLowerCase();
  const token = issuedToken?.id === workflowId ? issuedToken.value : '';
  const yaml = generatedYaml && generatedYaml.id === workflowId && generatedYaml.branch === branch ? generatedYaml.value : '';
  useEffect(() => () => yamlRequest.current?.abort(), []);

  const prepare = async (kind: 'token' | 'yaml') => {
    setPending(true);
    setFeedback('');
    const controller = new AbortController();
    if (kind === 'yaml') yamlRequest.current = controller;
    try {
      if (kind === 'token') {
        await withWorkspaceLock(async () => {
          const id = await save();
          if (id) setToken({ id, value: (await issueCiToken(id)).token });
        });
      } else {
        const id = await save();
        if (!id || controller.signal.aborted) return;
        const file = await getSecurityWorkflow(id, runner, controller.signal);
        const value = await file.text();
        if (!controller.signal.aborted) {
          setYaml({ id, branch, value });
          onDownload(file, 'security.yml');
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) setFeedback(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setPending(false);
      if (yamlRequest.current === controller) yamlRequest.current = undefined;
    }
  };

  const copy = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setFeedback(`${label} скопирован.`);
    } catch {
      setFeedback('Выделите и скопируйте значение из поля.');
    }
  };

  return (
    <section className="panel ci-setup" aria-label="GitHub Actions">
      <h2><Github size={18} aria-hidden="true" /> GitHub Actions</h2>
      <div className="ci-test-controls">
        <label>SHA коммита<input value={commit} onChange={event => setCommit(event.target.value)} placeholder="40 шестнадцатеричных символов" spellCheck={false} /></label>
        <button className="btn primary" disabled={!!busy || pending || !/^[0-9a-f]{40}$/.test(sha)} onClick={() => void runWorkflow(sha)}>
          <Play size={16} /> {busy === 'run' ? 'Выполнение…' : 'Проверить коммит'}
        </button>
      </div>
      <details>
        <summary>Настроить автоматический запуск</summary>
        <div className="ci-settings">
          <section>
            <h3>Секрет Actions</h3>
            <p>Сохраните токен как <code>CYBERFLOW_TOKEN</code>{githubUrl && <> в <a href={`${githubUrl}/settings/secrets/actions`} target="_blank" rel="noreferrer">секретах репозитория</a></>}. При выпуске нового токена прежний перестаёт действовать.</p>
            <button className="btn secondary" disabled={!!busy || pending} onClick={() => void prepare('token')}>Выпустить новый токен</button>
            {token && <div className="ci-test-controls"><label>Токен CI<input readOnly type="password" value={token} onFocus={event => event.target.select()} /></label><button className="btn secondary" onClick={() => void copy(token, 'Токен')}>Скопировать токен</button></div>}
          </section>
          <section>
            <h3>Файл workflow</h3>
            <p>Добавьте скачанный файл в <code>.github/workflows/security.yml</code>{githubUrl && <> в <a href={`${githubUrl}/actions/new`} target="_blank" rel="noreferrer">репозитории GitHub</a></>}.</p>
            <div className="ci-test-controls">
              <label>Runner для push
                <select value={runner} disabled={pending} onChange={event => { setRunner(event.target.value === 'hosted' ? 'hosted' : 'local'); setYaml(undefined); }}>
                  <option value="local">Своя машина · Windows</option>
                  <option value="hosted">GitHub-hosted · Ubuntu</option>
                </select>
              </label>
              <button className="btn secondary" disabled={!!busy || pending} onClick={() => void prepare('yaml')}><Download size={16} /> Скачать security.yml</button>
            </div>
            {runner === 'local' && <p className="muted">Назначьте Windows runner метку <code>cyberflow</code> на машине с CyberFlow.</p>}
            {yaml && <><textarea aria-label="Содержимое security.yml" readOnly rows={5} value={yaml} /><button className="btn secondary" onClick={() => void copy(yaml, 'Файл')}>Скопировать YAML</button></>}
          </section>
        </div>
        <p className="ci-pr-note">Для GitHub-hosted runner задайте переменную Actions <code>CYBERFLOW_API_URL</code> с публичным HTTPS-адресом API. После первого pull request добавьте <code>CyberFlow PR gate</code> в обязательные проверки {branch ? <>ветки <code>{branch}</code></> : 'целевой ветки'}.</p>
        {feedback && <p role="status">{feedback}</p>}
      </details>
    </section>
  );
}
