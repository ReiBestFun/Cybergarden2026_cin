import { api } from './client';
import type { ApiGraph } from './workflows';

export type McpToken = { id: string; name: string; expires_at: string };
export type IssuedMcpToken = McpToken & { token: string };

const tokenPath = (workspaceId: string) => `/api/workspaces/${workspaceId}/mcp-tokens`;
export const listMcpTokens = (workspaceId: string, signal: AbortSignal) => api<McpToken[]>(tokenPath(workspaceId), { signal });
export const createMcpToken = (workspaceId: string, name: string) =>
  api<IssuedMcpToken>(tokenPath(workspaceId), { method: 'POST', json: { name } });
export const revokeMcpToken = (workspaceId: string, tokenId: string) =>
  api<void>(`${tokenPath(workspaceId)}/${tokenId}`, { method: 'DELETE' });
export const validateImportedDraft = (workspaceId: string, graph: unknown, signal: AbortSignal) =>
  api<ApiGraph>(`/api/workspaces/${workspaceId}/workflow-drafts/validate`, { method: 'POST', json: graph, signal });
