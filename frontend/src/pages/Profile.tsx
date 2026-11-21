import { useEffect, useState, type FormEvent } from 'react';
import {
  createTelegramDestination, editProfile, listTelegramDestinations, removeTelegramDestination,
  renameTelegramDestination, sendTelegramCode, verifyTelegramDestination, type Profile, type TelegramDestination,
} from '../shared/api/account';
import { errorMessage } from '../shared/api/client';

export function AccountProfile({ profile, onChange }: { profile: Profile; onChange: (profile: Profile) => void }) {
  const [name, setName] = useState(profile.display_name);
  const [timezone, setTimezone] = useState(profile.timezone);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaved(false);
    setError('');
    try {
      const updated = await editProfile({ display_name: name.trim(), timezone: timezone.trim() });
      setName(updated.display_name);
      setTimezone(updated.timezone);
      onChange(updated);
      setSaved(true);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <h1>Профиль</h1>
      <form className="panel account-form" onSubmit={event => void save(event)}>
        <div className="profile-identity">
          {profile.avatar_url && <img src={profile.avatar_url} alt="" width={48} height={48} referrerPolicy="no-referrer" />}
          <div><h2>{profile.display_name}</h2><p className="muted">@{profile.github_login} · GitHub</p></div>
        </div>
        <div className="account-fields">
          <label>Отображаемое имя<input value={name} required maxLength={120} onChange={event => setName(event.target.value)} /></label>
          <label>Часовой пояс<input value={timezone} required maxLength={128} placeholder="Europe/Moscow"
            onChange={event => setTimezone(event.target.value)} /></label>
        </div>
        {error && <p className="notice error" role="alert">{error}</p>}
        {saved && <p className="notice success" role="status">Профиль сохранён.</p>}
        <button className="btn primary" disabled={saving}>Сохранить профиль</button>
      </form>
      <TelegramDestinations />
    </div>
  );
}

function TelegramDestinations() {
  const [destinations, setDestinations] = useState<TelegramDestination[] | null>(null);
  const [label, setLabel] = useState('');
  const [chat, setChat] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    listTelegramDestinations().then(result => {
      if (active) setDestinations(result.items);
    }, failure => { if (active) setError(errorMessage(failure)); });
    return () => { active = false; };
  }, []);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const destination = await createTelegramDestination({ label: label.trim(), chat_id: chat.trim() });
      setDestinations(items => [destination, ...(items ?? [])]);
      setLabel('');
      setChat('');
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <form className="panel account-form" onSubmit={event => void add(event)}>
        <h2>Telegram-чаты</h2>
        <p className="muted">Откройте диалог с ботом или добавьте его в группу. Привяжите числовой chat_id и подтвердите чат кодом.</p>
        <div className="account-fields">
          <label>Название чата<input value={label} required maxLength={120} onChange={event => setLabel(event.target.value)} /></label>
          <label>ID чата<input value={chat} required pattern="-?[0-9]{1,16}" maxLength={17} placeholder="-1001234567890"
            onChange={event => setChat(event.target.value)} /></label>
        </div>
        {error && <p className="notice error" role="alert">{error}</p>}
        <button className="btn primary" disabled={saving}>Добавить чат</button>
      </form>
      {destinations?.map(destination => (
        <TelegramRow key={destination.id} destination={destination}
          onUpdate={updated => setDestinations(items => items?.map(item => item.id === updated.id ? updated : item) ?? null)}
          onRemove={() => setDestinations(items => items?.filter(item => item.id !== destination.id) ?? null)} />
      ))}
    </>
  );
}

function TelegramRow({ destination, onUpdate, onRemove }: {
  destination: TelegramDestination;
  onUpdate: (updated: TelegramDestination) => void;
  onRemove: () => void;
}) {
  const [label, setLabel] = useState(destination.label);
  const [editing, setEditing] = useState(false);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const challengeActive = destination.challenge_expires_at && new Date(destination.challenge_expires_at).getTime() > Date.now();

  async function change(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try { await action(); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }

  return (
    <section className="panel account-form">
      <div className="account-row">
        <div>
          <h3>{destination.label}</h3>
          <p className="muted"><code>{destination.chat_id}</code> · {destination.verified_at ? 'Подтверждён' : 'Ожидает подтверждения'}</p>
        </div>
        <div className="toolbar-actions">
          {!destination.verified_at && <button className="btn primary" disabled={busy}
            onClick={() => void change(async () => onUpdate(await sendTelegramCode(destination.id)))}>
            {challengeActive ? 'Запросить новый код' : 'Отправить код'}
          </button>}
          <button className="btn secondary" disabled={busy} onClick={() => { setLabel(destination.label); setEditing(!editing); }}>Переименовать</button>
          <button className="btn secondary" disabled={busy} onClick={() => void change(async () => {
            await removeTelegramDestination(destination.id);
            onRemove();
          })}>Убрать чат</button>
        </div>
      </div>
      {editing && (
        <form className="account-inline-form" onSubmit={event => {
          event.preventDefault();
          void change(async () => {
            onUpdate(await renameTelegramDestination(destination.id, label.trim()));
            setEditing(false);
          });
        }}>
          <label>Название чата<input value={label} required maxLength={120} onChange={event => setLabel(event.target.value)} /></label>
          <button className="btn secondary" disabled={busy}>Сохранить название</button>
        </form>
      )}
      {!destination.verified_at && challengeActive && (
        <form className="account-inline-form" onSubmit={event => {
          event.preventDefault();
          void change(async () => {
            onUpdate(await verifyTelegramDestination(destination.id, code));
            setCode('');
          });
        }}>
          <label>Код из сообщения<input value={code} required pattern="[0-9]{8}" maxLength={8}
            autoComplete="one-time-code" inputMode="numeric" onChange={event => setCode(event.target.value)} /></label>
          <button className="btn secondary" disabled={busy}>Подтвердить чат</button>
        </form>
      )}
      {error && <p className="notice error" role="alert">{error}</p>}
    </section>
  );
}
