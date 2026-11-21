import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listTelegramDestinations, type TelegramDestination } from '../api/account';
import { errorMessage } from '../api/client';

export function TelegramRecipients({ value, onChange, saved, disabled }: {
  value: string; onChange: (value: string) => void; saved?: string[]; disabled?: boolean;
}) {
  const [destinations, setDestinations] = useState<TelegramDestination[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    listTelegramDestinations().then(page => {
      if (active) setDestinations(page.items.filter(chat => chat.verified_at));
    }, failure => {
      if (active) setError(errorMessage(failure));
    });
    return () => { active = false; };
  }, []);

  const selected = new Set(value.split('\n').map(chat => chat.trim()).filter(Boolean));
  const verified = new Set(destinations?.map(chat => chat.chat_id));
  const hasUnverified = !saved && destinations && [...selected].some(chat => !verified.has(chat));
  const options = [
    ...(destinations ?? []).map(chat => ({ id: chat.chat_id, label: chat.label })),
    ...(saved ?? []).filter(id => !verified.has(id)).map(id => ({ id, label: 'Сохранённый чат ' + id })),
  ];
  return (
    <fieldset className="telegram-picker" disabled={disabled}>
      <legend>Получатели Telegram</legend>
      {error && <small role="alert">{error}</small>}
      {!destinations && !error && <small className="muted">Загрузка чатов…</small>}
      {destinations?.length === 0 && <p className="muted">Добавьте и подтвердите Telegram-чат в профиле.</p>}
      {options.map(chat => (
        <label key={chat.id}>
          <input type="checkbox" checked={selected.has(chat.id)} onChange={event => {
            if (event.target.checked) selected.add(chat.id);
            else selected.delete(chat.id);
            onChange([...selected].join('\n'));
          }} />
          {chat.label}
        </label>
      ))}
      {hasUnverified && (
        <button type="button" className="btn secondary" onClick={() => onChange([...selected].filter(chat => verified.has(chat)).join('\n'))}>
          Убрать неподтверждённые чаты
        </button>
      )}
      <Link to="/profile">Управлять чатами</Link>
    </fieldset>
  );
}
