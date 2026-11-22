import { api } from './client';
import type { ApiGraph, Page, ScheduleSettings, Workflow } from './workflows';

export type WorkflowTemplate = {
  id: string;
  workspace_id: string | null;
  draft_source_workspace_id: string | null;
  name: string;
  description: string;
  graph: ApiGraph;
  schedule: ScheduleSettings | null;
  created_at: string | null;
  updated_at: string | null;
  can_edit: boolean;
  is_builtin: boolean;
};

export const listTemplates = (workspaceId: string, offset: number, limit: number, signal: AbortSignal) =>
  api<Page<WorkflowTemplate>>(`/api/templates?workspace_id=${workspaceId}&limit=${limit}&offset=${offset}`, { signal });

export const publishTemplate = (workflowId: string, name: string, description: string, workspaceId?: string) =>
  api<WorkflowTemplate>('/api/templates', { method: 'POST', json: { workflow_id: workflowId, name, description, workspace_id: workspaceId } });

export const copyTemplate = (id: string, projectId: string, name: string, signal?: AbortSignal) =>
  api<Workflow>(`/api/templates/${id}/copy`, { method: 'POST', json: { project_id: projectId, name }, signal });

export const publishDraft = (id: string, workspaceId: string) =>
  api<WorkflowTemplate>(`/api/templates/${id}/publish`, { method: 'POST', json: { workspace_id: workspaceId } });

export const editTemplate = (id: string, data: Pick<WorkflowTemplate, 'name' | 'description'>) =>
  api<WorkflowTemplate>(`/api/templates/${id}`, { method: 'PATCH', json: data });

export const removeTemplate = (id: string) =>
  api<void>(`/api/templates/${id}`, { method: 'DELETE' });
