import type { Edge } from '@xyflow/react';
import type { FlowNode } from './types';

export const DEFAULT_REPOSITORY = 'https://github.com/TBagirov/training';
export const DEFAULT_NAME = 'Security check workflow';
const DRAFT_KEY = 'cyberflow.workflow.v2';

const node = (id: string, kind: string, label: string, x: number, y: number): FlowNode =>
  ({ id, type: 'flowNode', position: { x, y }, data: { kind, label, config: {} } });
const edge = (id: string, source: string, sourceHandle: string, target: string, targetHandle: string): Edge =>
  ({ id, source, sourceHandle, target, targetHandle, style: { stroke: '#a7a0b8', strokeWidth: 2 } });

// A runnable graph that matches the backend contract.
export const initialNodes: FlowNode[] = [
  node('trigger', 'manual_trigger', 'Ручной запуск', 40, 200),
  node('repo', 'repository', 'Репозиторий', 320, 200),
  node('secrets', 'scan_secrets', 'Secrets', 620, 60),
  node('sca', 'scan_sca', 'SCA', 620, 200),
  node('sast', 'scan_sast', 'SAST', 620, 340),
  node('merge', 'merge_findings', 'Объединение находок', 920, 200),
];

export const initialEdges: Edge[] = [
  edge('e-trigger-repo', 'trigger', 'params', 'repo', 'params'),
  edge('e-repo-secrets', 'repo', 'snapshot', 'secrets', 'snapshot'),
  edge('e-repo-sca', 'repo', 'snapshot', 'sca', 'snapshot'),
  edge('e-repo-sast', 'repo', 'snapshot', 'sast', 'snapshot'),
  edge('e-secrets-merge', 'secrets', 'result', 'merge', 'results'),
  edge('e-sca-merge', 'sca', 'result', 'merge', 'results'),
  edge('e-sast-merge', 'sast', 'result', 'merge', 'results'),
];

export type Draft = {
  nodes: FlowNode[];
  edges: Edge[];
  project: string;
  name: string;
  projectId: string | null;
  workflowId: string | null;
  schedule?: { time: string; enabled: boolean };
};

export function getSaved(): Draft {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (raw) {
      const p = JSON.parse(raw);
      if (Array.isArray(p.nodes) && Array.isArray(p.edges)) {
        return { project: DEFAULT_REPOSITORY, name: DEFAULT_NAME, projectId: null, workflowId: null, ...p };
      }
    }
  } catch { /* corrupted draft */ }
  return { nodes: initialNodes, edges: initialEdges, project: DEFAULT_REPOSITORY, name: DEFAULT_NAME, projectId: null, workflowId: null };
}

export function saveDraft(draft: Draft) {
  try {
    const nodes = draft.nodes.map(n => ({ ...n, data: { ...n.data, status: undefined } }));
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...draft, nodes }));
  } catch { /* storage unavailable */ }
}

const LAST_RUN_KEY = 'cyberflow.lastRunId';
export const getLastRunId = () => { try { return localStorage.getItem(LAST_RUN_KEY); } catch { return null; } };
export const setLastRunId = (id: string) => { try { localStorage.setItem(LAST_RUN_KEY, id); } catch { /* ignore */ } };
