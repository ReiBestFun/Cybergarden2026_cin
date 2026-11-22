import { api } from './client';

export type DeliveryChannel = 'email' | 'telegram' | 'slack';
export type ChannelSettings = { version: number; recipients: string[]; has_credentials: boolean };
type DeliverySettings = Record<DeliveryChannel, ChannelSettings | null>;
type ChannelChange = { version: number; recipients: string[]; credentials?: { secret: string; username?: string } | null };

const path = (workspaceId: string) => `/api/workspaces/${workspaceId}/delivery`;
export const getWorkspaceDelivery = (workspaceId: string, signal: AbortSignal) =>
  api<DeliverySettings>(path(workspaceId), { signal });
export const updateWorkspaceDelivery = (workspaceId: string, channel: DeliveryChannel, change: ChannelChange) =>
  api<ChannelSettings>(`${path(workspaceId)}/${channel}`, { method: 'PATCH', json: change });
