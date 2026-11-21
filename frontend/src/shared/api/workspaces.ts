import { api, listAll } from './client';

export type Workspace = { id: string; name: string; personal: boolean; role: 'owner' | 'member' };

export const listWorkspaces = (signal?: AbortSignal) => listAll<Workspace>('/api/workspaces', {}, signal);
export const createWorkspace = (name: string) => api<Workspace>('/api/workspaces', { method: 'POST', json: { name } });
