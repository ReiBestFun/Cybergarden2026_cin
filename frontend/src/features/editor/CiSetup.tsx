import { useState } from 'react';
import { Play } from 'lucide-react';
import { errorMessage } from '../../shared/api/client';
import { getSecurityWorkflow, issueCiToken } from '../../shared/api/workflows';
import { DEFAULT_REPOSITORY, TRAINING_CI_SHA } from './defaults';
import { useEditorStore } from './store';

export function CiSetup() {
  const { project, nodes, workflowId, busy, save, runWorkflow } = useEditorStore();
  const repository = project.trim().replace(/\/$/, '').replace(/\.git$/, '');
  const githubUrl = /^https:\/\/github\.com\/[a-z\d-]+\/[a-z\d_.-]+$/i.test(repository) ? repository : null;
  const branch = nodes.find(node => node.data.kind === 'ci_trigger')?.data.config?.branch || 'main';
  const [commit, setCommit] = useState(repository.toLowerCase() === DEFAULT_REPOSITORY.toLowerCase() ? TRAINING_CI_SHA : '');
  const [issuedToken, setToken] = useState<{ id: string; value: string }>();
  const [generatedYaml, setYaml] = useState<{ id: string; value: string }>();
  const [runner, setRunner] = useState<'local' | 'hosted'>('local');
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState('');
  const sha = commit.trim().toLowerCase();
  const token = issuedToken?.id === workflowId ? issuedToken.value : '';
  const yaml = generatedYaml?.id === workflowId ? generatedYaml.value : '';

  const prepare = async (kind: 'token' | 'yaml') => {
    setPending(true);
    setFeedback('');
    try {
      const id = await save();
      if (!id) return;
      if (kind === 'token') setToken({ id, value: (await issueCiToken(id)).token });
      else setYaml({ id, value: await (await getSecurityWorkflow(id, runner)).text() });
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setPending(false);
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
    <section className="panel ci-setup" aria-label="CI/CD">
      <div className="ci-test-controls">
        <label>SHA коммита<input value={commit} onChange={event => setCommit(event.target.value)} placeholder="40 символов" spellCheck={false} /></label>
        <button className="btn primary" disabled={!!busy || pending || !/^[0-9a-f]{40}$/.test(sha)} onClick={() => void runWorkflow(sha)}>
          <Play size={16} /> {busy === 'run' ? 'Выполнение…' : 'Проверить коммит в CI'}
        </button>
      </div>
      <details>
        <summary>Подключить автоматический запуск из GitHub</summary>
        <p>Выпустите токен и сохраните его как секрет <code>CYBERFLOW_TOKEN</code>{githubUrl && <> в <a href={`${githubUrl}/settings/secrets/actions`} target="_blank" rel="noreferrer">настройках GitHub Actions</a></>}.</p>
        <p className="muted">Новый токен заменяет предыдущий. Сохраните его сразу после выпуска.</p>
        <button className="btn secondary" disabled={!!busy || pending} onClick={() => void prepare('token')}>Выпустить новый токен</button>
        {token && <div className="ci-test-controls"><label>Токен CI<input readOnly type="password" value={token} onFocus={event => event.target.select()} /></label><button className="btn secondary" onClick={() => void copy(token, 'Токен')}>Скопировать токен</button></div>}
        <p>Создайте файл <code>.github/workflows/security.yml</code>{githubUrl && <> <a href={`${githubUrl}/new/${encodeURIComponent(branch)}`} target="_blank" rel="noreferrer">на сайте GitHub</a></>} и вставьте готовое содержимое.</p>
        <label>Runner для пушей{' '}
          <select value={runner} disabled={pending} onChange={event => { setRunner(event.target.value === 'hosted' ? 'hosted' : 'local'); setYaml(undefined); }}>
            <option value="local">Windows · localhost</option>
            <option value="hosted">GitHub-hosted · HTTPS</option>
          </select>
        </label>{' '}
        <button className="btn secondary" disabled={!!busy || pending} onClick={() => void prepare('yaml')}>Подготовить содержимое</button>
        {yaml && <><textarea aria-label="Содержимое security.yml" readOnly rows={4} value={yaml} /><button className="btn secondary" onClick={() => void copy(yaml, 'Файл')}>Скопировать security.yml</button></>}
        {runner === 'local' && <p className="muted">Для пушей нужен Windows runner с меткой <code>cyberflow</code> на компьютере с CyberFlow.</p>}
        <p>Для pull request укажите доступный GitHub-hosted runner HTTPS-адрес API в переменной Actions <code>CYBERFLOW_API_URL</code>. После первой проверки PR добавьте <code>CyberFlow PR gate</code> в обязательные проверки ветки <code>{branch}</code>.</p>
        {feedback && <p role="status">{feedback}</p>}
      </details>
    </section>
  );
}
