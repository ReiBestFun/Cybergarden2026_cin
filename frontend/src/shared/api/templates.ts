import { api } from './client';
import type { ApiGraph, Page, ScheduleSettings, Workflow } from './workflows';

export type WorkflowTemplate = {
  id: string;
  name: string;
  description: string;
  graph: ApiGraph;
  schedule: ScheduleSettings | null;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
};

export const listTemplates = (offset: number, limit: number, signal: AbortSignal) =>
  api<Page<WorkflowTemplate>>(`/api/templates?limit=${limit}&offset=${offset}`, { signal });

export const publishTemplate = (workflowId: string, name: string, description: string) =>
  api<WorkflowTemplate>('/api/templates', { method: 'POST', json: { workflow_id: workflowId, name, description } });

export const copyTemplate = (id: string, projectId: string, name: string, signal?: AbortSignal) =>
  api<Workflow>(`/api/templates/${id}/copy`, { method: 'POST', json: { project_id: projectId, name }, signal });

export const editTemplate = (id: string, data: Pick<WorkflowTemplate, 'name' | 'description'>) =>
  api<WorkflowTemplate>(`/api/templates/${id}`, { method: 'PATCH', json: data });

export const removeTemplate = (id: string) =>
  api<void>(`/api/templates/${id}`, { method: 'DELETE' });
