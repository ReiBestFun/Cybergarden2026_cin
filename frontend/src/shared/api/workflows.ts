import { ApiError, api, apiFile, apiUrl, listAll } from './client';

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type ApiGraph = {
  nodes: { id: string; type: string; config: Record<string, JsonValue>; position: { x: number; y: number } }[];
  edges: { id: string; source: string; source_port: string; target: string; target_port: string }[];
};

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type Project = { id: string; workspace_id: string; name: string; repository_url: string; default_ref: string; created_at: string };
export type Workflow = { id: string; project_id: string; name: string; graph: ApiGraph; created_at: string; updated_at: string };

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed';
export type NodeStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';
export type RunError = {
  code: string; message: string; node_id?: string | null; field?: string | null;
  path?: string | null; line?: number | null; hint?: string | null;
};
export type RunSummary = { total: number; high_count: number; critical_count: number };

export type Run = {
  id: string; project_id: string; workflow_id: string; trigger: 'manual' | 'schedule' | 'ci'; status: RunStatus;
  source_snapshot: Record<string, string>; commit_sha: string | null; created_at: string;
  started_at: string | null; finished_at: string | null; summary: RunSummary | null; error: RunError | null;
};
export type NodeRun = {
  node_id: string; type: string; status: NodeStatus; started_at: string | null; finished_at: string | null;
  active_output_ports: string[]; input_preview: Record<string, JsonValue> | null;
  output_preview: Record<string, JsonValue> | null; error: RunError | null;
};
export type RunDetail = Run & { graph_snapshot: ApiGraph; nodes: NodeRun[]; delivery?: RunReport['delivery'] };

export type FindingVerification = {
  status: 'confirmed' | 'needs_review' | 'false_positive';
  code: string;
  message: string;
};

export type Finding = {
  id: string; node_run_id: string; rule_id: string; category: 'secrets' | 'sca' | 'sast';
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info'; title: string; description: string; remediation: string;
  path: string; line: number | null; end_line: number | null; evidence: string; metadata: Record<string, string>;
  verification?: FindingVerification | null;
};
type ScanCheck = { category: Finding['category']; status: NodeStatus; warnings: RunError[] };
type RecipientDelivery = {
  id?: string; recipient: string; status: string; phase?: 'message' | 'attachment'; attempts?: number;
  message_id?: string | null; attachment_id?: string | null; error_code?: string | null; error?: string | null;
};
type NodeDelivery = { channel?: string; status: string; error?: RunError | null; recipients?: RecipientDelivery[] };
export type RunReport = {
  llm?: { text?: string; error?: RunError | null } | null;
  status: RunStatus; summary: RunSummary;
  coverage: { status?: string; checks?: Record<string, ScanCheck> }; error: RunError | null;
  delivery?: Record<string, NodeDelivery> | null;
};
export type Frequency = 'daily' | 'weekly' | 'monthly' | 'cron';
// Weekdays: 0 = Monday … 6 = Sunday.
export type ScheduleSettings = {
  frequency: Frequency; time?: string; weekdays?: number[]; day_of_month?: number; cron?: string; timezone: string;
};
export type GeneratedWorkflow = {
  name: string;
  graph: ApiGraph;
  schedule: Omit<Schedule, 'enabled' | 'next_run_at' | 'upcoming' | 'last_enqueued_at' | 'last_error'> | null;
  repository_url: string | null;
  issues: RunError[];
};
export type Schedule = {
  enabled: boolean; frequency: Frequency; time: string | null; weekdays: number[] | null; day_of_month: number | null;
  cron: string | null; timezone: string | null; next_run_at: string | null; upcoming: string[];
  last_enqueued_at: string | null; last_error: RunError | null;
};

export const isFinished = (status: RunStatus) => status !== 'queued' && status !== 'running';

export const canonicalRepo = (url: string) => url.trim().replace(/\/+$/, '').replace(/\.git$/i, '').toLowerCase();

export const listProjects = (workspaceId: string, signal?: AbortSignal) =>
  listAll<Project>('/api/projects', { workspace_id: workspaceId }, signal);
export const getProject = (id: string, signal?: AbortSignal) => api<Project>(`/api/projects/${id}`, { signal });
export const createProject = (data: { workspace_id: string; name: string; repository_url: string; default_ref?: string }, signal?: AbortSignal) =>
  api<Project>('/api/projects', { method: 'POST', json: data, signal });
export const moveProject = (id: string, workspaceId: string) =>
  api<Project>(`/api/projects/${id}/move`, { method: 'POST', json: { workspace_id: workspaceId } });
export const updateProject = (id: string, data: Pick<Project, 'name' | 'default_ref'>) =>
  api<Project>(`/api/projects/${id}`, { method: 'PATCH', json: data });

export async function ensureProject(workspaceId: string, repositoryUrl: string, knownId?: string | null, signal?: AbortSignal): Promise<Project> {
  const wanted = canonicalRepo(repositoryUrl);
  if (knownId) {
    try {
      const project = await getProject(knownId, signal);
      if (canonicalRepo(project.repository_url) === wanted) {
        if (project.workspace_id !== workspaceId) {
          throw new ApiError(409, { code: 'PROJECT_MOVED', message: 'Проект перенесён. Откройте сценарий в его новом пространстве.' });
        }
        return project;
      }
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
  }
  const existing = (await listProjects(workspaceId, signal)).find(p => canonicalRepo(p.repository_url) === wanted);
  if (existing) return existing;
  const name = repositoryUrl.trim().replace(/\/+$/, '').split('/').slice(-2).join('/') || 'project';
  return createProject({ workspace_id: workspaceId, name, repository_url: repositoryUrl.trim() }, signal);
}

export const listWorkflows = (projectId: string, signal?: AbortSignal) =>
  listAll<Workflow>(`/api/projects/${projectId}/workflows`, {}, signal);
export const generateWorkflow = (description: string, timezone: string, workspaceId: string, signal: AbortSignal) =>
  api<GeneratedWorkflow>('/api/workflows/generate?' + new URLSearchParams({ workspace_id: workspaceId }), {
    method: 'POST', json: { description, timezone }, signal,
  });
export const getWorkflow = (id: string, signal?: AbortSignal) => api<Workflow>(`/api/workflows/${id}`, { signal });
export const createWorkflow = (projectId: string, name: string, graph: ApiGraph, signal?: AbortSignal) =>
  api<Workflow>(`/api/projects/${projectId}/workflows`, { method: 'POST', json: { name, graph }, signal });
export const updateWorkflow = (id: string, name: string, graph: ApiGraph, signal?: AbortSignal) =>
  api<Workflow>(`/api/workflows/${id}`, { method: 'PATCH', json: { name, graph }, signal });
export const validateWorkflow = (id: string, signal?: AbortSignal) =>
  api<{ valid: boolean; issues: RunError[] }>(`/api/workflows/${id}/validate`, { method: 'POST', json: {}, signal });
export const getSecurityWorkflow = (id: string, mode: 'local' | 'hosted' = 'local', signal?: AbortSignal) =>
  apiFile(`/api/workflows/${id}/security.yml?mode=${mode}`, signal);
export const issueCiToken = (id: string) => api<{ token: string }>(`/api/workflows/${id}/ci-token`, { method: 'POST', json: {} });
export const startCiTest = (id: string, commitSha: string, signal?: AbortSignal) =>
  api<{ id: string }>(`/api/workflows/${id}/ci-test`, { method: 'POST', json: { commit_sha: commitSha }, signal });
export const getSchedule = (id: string, signal?: AbortSignal) => api<Schedule>(`/api/workflows/${id}/schedule`, { signal });
export const setSchedule = (id: string, body: { enabled: false } | ({ enabled: boolean } & ScheduleSettings), signal?: AbortSignal) =>
  api<Schedule>(`/api/workflows/${id}/schedule`, { method: 'PUT', json: body, signal });
export const previewSchedule = (settings: ScheduleSettings, signal?: AbortSignal) =>
  api<{ next_runs: string[] }>('/api/schedules/preview', { method: 'POST', json: settings, signal });

export const startRun = (workflowId: string, signal?: AbortSignal) =>
  api<{ id: string; status: 'queued' }>(`/api/workflows/${workflowId}/runs`, { method: 'POST', json: {}, signal });
export const getRun = (id: string, signal?: AbortSignal) => api<RunDetail>(`/api/runs/${id}`, { signal });
export const listRuns = (projectId: string, signal?: AbortSignal) =>
  listAll<Run>(`/api/projects/${projectId}/runs`, {}, signal);
export const listFindings = (runId: string, signal?: AbortSignal) => api<Page<Finding>>(`/api/runs/${runId}/findings?limit=200`, { signal });
export const getReport = (runId: string, signal?: AbortSignal) => api<RunReport>(`/api/runs/${runId}/report`, { signal });
export const exportUrl = (runId: string, format: 'json' | 'markdown') => apiUrl(`/api/runs/${runId}/export?format=${format}`);
