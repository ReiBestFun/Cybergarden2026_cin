import type { Edge } from '@xyflow/react';
import type { FlowNode } from './types';
export const initialNodes: FlowNode[] = [
  { id: 'n1', type: 'flowNode', position: { x: 130, y: 100 }, data: { kind: 'manual', label: 'Ручной запуск' } },
  { id: 'n2', type: 'flowNode', position: { x: 420, y: 100 }, data: { kind: 'secrets', label: 'Secrets Scanner' } },
  { id: 'n3', type: 'flowNode', position: { x: 710, y: 100 }, data: { kind: 'ai', label: 'AI Analysis' } },
  { id: 'n4', type: 'flowNode', position: { x: 1000, y: 100 }, data: { kind: 'telegram', label: 'Telegram' } },
];

export const initialEdges: Edge[] = [
  { id: 'e1', source: 'n1', target: 'n2' },
  { id: 'e2', source: 'n2', target: 'n3' },
  { id: 'e3', source: 'n3', target: 'n4' },
];

export function getSaved(): { nodes: FlowNode[]; edges: Edge[]; project?: string } {
  try {
    const raw = localStorage.getItem('cyberflow.workflow');
    if (raw) {
      const p = JSON.parse(raw);
      if (Array.isArray(p.nodes) && Array.isArray(p.edges)) return p;
    }
  } catch {}
  return { nodes: initialNodes, edges: initialEdges };
}

