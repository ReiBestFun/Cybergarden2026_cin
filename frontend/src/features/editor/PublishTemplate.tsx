import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage } from '../../shared/api/client';
import { publishTemplate } from '../../shared/api/templates';
import { useEditorStore } from './store';

export function PublishTemplate({ onClose }: { onClose: () => void }) {
  const { name, save, busy } = useEditorStore();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [published, setPublished] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving || busy) return;
    const fields = new FormData(event.currentTarget);
    setSaving(true);
    setError('');
    try {
      const id = await save();
      if (id) {
        await publishTemplate(id, String(fields.get('name')), String(fields.get('description')));
        setPublished(true);
      }
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="panel template-panel" aria-label="Сохранить личный шаблон">
      <h3>Сохранить сценарий как шаблон</h3>
      {published ? <div className="toolbar-actions">
        <Link className="btn primary" to="/templates">Открыть библиотеку</Link>
        <button className="btn secondary" onClick={onClose}>Закрыть</button>
      </div> : <form onSubmit={submit}>
        <label>Название<input name="name" defaultValue={name} maxLength={120} required /></label>
        <label>Описание<textarea name="description" maxLength={1000} rows={2} /></label>
        <p className="muted">Учётные данные и адресаты уведомлений исключаются из шаблона.</p>
        {error && <p className="notice error" role="alert">{error}</p>}
        <div className="toolbar-actions">
          <button className="btn primary" disabled={saving || !!busy}>{saving ? 'Сохранение…' : 'Сохранить шаблон'}</button>
          <button className="btn secondary" type="button" disabled={saving} onClick={onClose}>Отмена</button>
        </div>
      </form>}
    </section>
  );
}
