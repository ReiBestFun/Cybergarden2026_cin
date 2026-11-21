import { useEffect, useState, type FormEvent } from 'react';
import {
  addPublicTelegramChat, startTelegramLink, telegramLinkStatus, type TelegramLink,
  editProfile, listTelegramDestinations, removeTelegramDestination,
  renameTelegramDestination, sendTelegramCode, verifyTelegramDestination, type Profile, type TelegramDestination,
} from '../shared/api/account';
import { ApiError, errorMessage } from '../shared/api/client';

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
  const [username, setUsername] = useState('');
  const [link, setLink] = useState<TelegramLink | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    listTelegramDestinations().then(result => {
      if (active) setDestinations(items => [...(items ?? []), ...result.items.filter(chat => !items?.some(item => item.id === chat.id))]);
    }, failure => { if (active) setError(errorMessage(failure)); });
    return () => { active = false; };
  }, []);

  function remember(destination: TelegramDestination) {
    setDestinations(items => [destination, ...(items ?? []).filter(item => item.id !== destination.id)]);
  }

  useEffect(() => {
    if (!link) return;
    let active = true;
    let timer: number;
    async function check(current: TelegramLink) {
      if (!active) return;
      if (Date.now() >= Date.parse(current.expires_at)) {
        setLink(null);
        setError('Ссылка истекла. Создайте новую.');
        return;
      }
      try {
        const dest = await telegramLinkStatus(current.id);
        if (!active) return;
        if (dest) {
          remember(dest);
          setError('');
          if (dest.verified_at) {
            setNotice('Telegram подключён.');
          } else {
            setNotice('Чат найден. Введите в профиле код из сообщения бота.');
            try {
              const challenged = await sendTelegramCode(dest.id);
              if (active) remember(challenged);
            } catch (failure) {
              if (active) { setNotice('Чат найден. Запросите код в карточке чата.'); setError(errorMessage(failure)); }
            }
          }
          if (active) setLink(null);
          return;
        }
      } catch (failure) {
        if (!active) return;
        setError(errorMessage(failure));
        if (failure instanceof ApiError && [403, 404, 410].includes(failure.status)) {
          setLink(null);
          return;
        }
      }
      timer = window.setTimeout(() => void check(current), 2500);
    }
    void check(link);
    return () => { active = false; window.clearTimeout(timer); };
  }, [link]);

  async function connect(group: boolean) {
    setSaving(true);
    setError('');
    setNotice('');
    setLink(null);
    try { setLink(await startTelegramLink(group)); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setSaving(false); }
  }

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setNotice('');
    setError('');
    try {
      remember(await addPublicTelegramChat(username.trim()));
      setUsername('');
      setNotice('Чат подключён.');
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setSaving(false); }
  }

  return (
    <>
      <section className="panel account-form">
        <h2>Telegram-чаты</h2>
        <div className="toolbar-actions">
          <button className="btn primary" disabled={saving} onClick={() => void connect(false)}>Подключить личный Telegram</button>
          <button className="btn secondary" disabled={saving} onClick={() => void connect(true)}>Подключить группу</button>
        </div>
        {link && <div className="account-form">
          <p>Откройте бота, нажмите «Запустить» или выберите группу, затем вернитесь сюда за подтверждением. Ссылка действует 10 минут.</p>
          <a className="btn primary" href={link.url} target="_blank" rel="noreferrer">Открыть Telegram</a>
        </div>}
        <details><summary>Подключить публичный канал или супергруппу по имени</summary>
          <form className="account-form" onSubmit={event => void add(event)}>
            <p className="muted">Подключите личный Telegram администратора чата. Дайте боту право отправлять сообщения в группу или публиковать записи в канале.</p>
            <label>Имя канала или супергруппы<input value={username} required maxLength={33} placeholder="@my_channel"
              onChange={event => setUsername(event.target.value)} /></label>
            <button className="btn secondary" disabled={saving}>Подключить по имени</button>
          </form>
        </details>
        {notice && <p className="notice success" role="status">{notice}</p>}
        {error && <p className="notice error" role="alert">{error}</p>}
      </section>
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
