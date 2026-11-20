import type { Connection, Edge } from '@xyflow/react';
import type { FlowNode } from './types';
import type { NodeTypeDefinition } from '../../shared/api/nodeTypes';

type Port = { id: string; type?: string };

export function validateConnection(
  connection: Connection,
  nodes: FlowNode[],
  edges: Edge[],
  definitions: NodeTypeDefinition[],
): boolean {
  const { source, target, sourceHandle, targetHandle } = connection;
  if (!source || !target || source === target) return false;
  const from = nodes.find(n => n.id === source);
  const to = nodes.find(n => n.id === target);
  if (!from || !to) return false;
  const fromType = definitions.find(t => t.kind === from.data.kind);
  const toType = definitions.find(t => t.kind === to.data.kind);
  if (!fromType || !toType) return false;
  const outputs: Port[] = fromType.outputs ?? [{ id: 'out' }];
  const inputs: Port[] = toType.inputs ?? [{ id: 'in' }];
  // A missing handle ID is supported only for a single-port legacy node.
  const output = sourceHandle ? outputs.find(p => p.id === sourceHandle) : outputs.length === 1 ? outputs[0] : undefined;
  const input = targetHandle ? inputs.find(p => p.id === targetHandle) : inputs.length === 1 ? inputs[0] : undefined;
  if (!output || !input) return false;
  const sourceType = output.type ?? 'any';
  const targetType = input.type ?? 'any';
  if (sourceType !== 'any' && targetType !== 'any' && sourceType !== targetType) return false;
  if (edges.some(e => e.source === source && e.target === target &&
      (e.sourceHandle ?? outputs[0]?.id) === output.id &&
      (e.targetHandle ?? inputs[0]?.id) === input.id)) return false;
  // Adding source -> target creates a cycle if target can already reach source.
  const visited = new Set<string>();
  const stack = [target];
  while (stack.length) {
    const current = stack.pop()!;
    if (current === source) return false;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const edge of edges) if (edge.source === current) stack.push(edge.target);
  }
  return true;
}
