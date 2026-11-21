import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '../../shared/api/client';
import { listCredentials, type Credential, type CredentialKind } from '../../shared/api/credentials';
import { canonicalRepo, getProject } from '../../shared/api/workflows';
import { useEditorStore } from './store';

export function CredentialPicker({ kind, value }: { kind: CredentialKind; value: string }) {
  const { projectId, project, updateConfig } = useEditorStore();
  const repo = canonicalRepo(project);
  const [options, setOptions] = useState<Credential[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setReady(false);
    setOptions([]);
    setError('');
    if (projectId) {
      void getProject(projectId).then(async saved => {
        if (controller.signal.aborted || repo !== canonicalRepo(saved.repository_url)) return;
        const items = await listCredentials(projectId, controller.signal);
        if (controller.signal.aborted) return;
        setOptions(items.filter(item => item.active && item.kind === kind));
        setReady(true);
      }).catch(failure => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    }
    return () => controller.abort();
  }, [projectId, repo, kind]);

  return (
    <div className="credential-picker">
      <label>Учётные данные
        <select value={value} onChange={event => updateConfig('credential_id', event.target.value)}>
          <option value="">Настройки сервера</option>
          {value && !options.some(item => item.id === value) && <option value={value}>{ready ? 'Запись недоступна' : 'Выбранная запись'}</option>}
          {options.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      {error && <small role="alert">{error}</small>}
      {ready && <Link to={`/credentials?project=${projectId}`}>Управлять учётными данными</Link>}
      {!ready && !error && <small className="muted">Сохрани сценарий, чтобы загрузить учётные данные проекта.</small>}
    </div>
  );
}
