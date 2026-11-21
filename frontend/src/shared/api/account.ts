import { api } from './client';
import type { Page } from './workflows';

export type Profile = {
  id: string;
  github_login: string;
  display_name: string;
  avatar_url: string | null;
  timezone: string;
};
export type TelegramDestination = {
  id: string;
  label: string;
  chat_id: string;
  verified_at: string | null;
  challenge_expires_at: string | null;
};
export const getProfile = () => api<Profile>('/api/me');
export const editProfile = (data: Pick<Profile, 'display_name' | 'timezone'>) =>
  api<Profile>('/api/me', { method: 'PATCH', json: data });
export const logout = () => api<void>('/api/auth/logout', { method: 'POST' });

const TELEGRAM = '/api/telegram-destinations';
export const listTelegramDestinations = () => api<Page<TelegramDestination>>(TELEGRAM + '?limit=200');
export const createTelegramDestination = (data: Pick<TelegramDestination, 'label' | 'chat_id'>) =>
  api<TelegramDestination>(TELEGRAM, { method: 'POST', json: data });
export const renameTelegramDestination = (id: string, label: string) =>
  api<TelegramDestination>(`${TELEGRAM}/${id}`, { method: 'PATCH', json: { label } });
export const removeTelegramDestination = (id: string) => api<void>(`${TELEGRAM}/${id}`, { method: 'DELETE' });
export const sendTelegramCode = (id: string) => api<TelegramDestination>(`${TELEGRAM}/${id}/challenge`, { method: 'POST' });
export const verifyTelegramDestination = (id: string, code: string) =>
  api<TelegramDestination>(`${TELEGRAM}/${id}/verify`, { method: 'POST', json: { code } });
