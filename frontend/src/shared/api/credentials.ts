import { api } from './client';
import type { Page } from './workflows';

export const credentialKinds = {
  telegram: { label: 'Токен Telegram', ready: true },
  smtp: { label: 'Пароль SMTP', ready: true },
  github_token: { label: 'Токен GitHub', ready: false },
  ssh_key: { label: 'Приватный ключ SSH', ready: false },
  llm: { label: 'Ключ API для LLM', ready: false },
  webhook: { label: 'Секрет исходящего webhook', ready: false },
  webhook_url: { label: 'Закрытый URL webhook', ready: false },
};

export type CredentialKind = keyof typeof credentialKinds;
export type Credential = { id: string; project_id: string; name: string; kind: CredentialKind; active: boolean };
export type CredentialValue = { secret: string; username?: string; passphrase?: string };

const path = (projectId: string) => `/api/projects/${projectId}/credentials`;

export async function listCredentials(projectId: string, signal?: AbortSignal): Promise<Credential[]> {
  const items: Credential[] = [];
  for (;;) {
    const page = await api<Page<Credential>>(`${path(projectId)}?limit=200&offset=${items.length}`, { signal });
    items.push(...page.items);
    if (items.length >= page.total || page.items.length === 0) return items;
  }
}

export const createCredential = (projectId: string, data: { name: string; kind: CredentialKind; value: CredentialValue }) =>
  api<Credential>(path(projectId), { method: 'POST', json: data });

export const updateCredential = (projectId: string, id: string, data: { value?: CredentialValue; active?: boolean }) =>
  api<Credential>(`${path(projectId)}/${id}`, { method: 'PATCH', json: data });
