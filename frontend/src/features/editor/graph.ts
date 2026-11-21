import type { Edge } from '@xyflow/react';
import type { ConditionRow, FlowData, FlowNode } from './types';
import type { NodeTypeDefinition, ParamField } from '../../shared/api/nodeTypes';
import type { ApiGraph, JsonValue } from '../../shared/api/workflows';

const allowsInteger = (field?: ParamField) =>
  field?.type === 'integer' || field?.type === 'number' || !!field?.anyOf?.some(x => x.type === 'integer' || x.type === 'number');

// Form values are strings; the backend expects typed JSON.
// Lists are edited as text, one item per line (commas and semicolons also separate items).
export const splitList = (raw: string) => raw.split(/[\n,;]+/).map(item => item.trim()).filter(Boolean);

function toConfigValue(field: ParamField | undefined, raw: string): JsonValue {
  if (field?.type === 'array') return splitList(raw);
  if (field?.type === 'boolean') return raw === 'true';
  if (allowsInteger(field) && /^-?\d+$/.test(raw.trim())) return Number(raw.trim());
  if ((field?.type === 'number') && raw.trim() !== '' && !Number.isNaN(Number(raw))) return Number(raw);
  return raw;
}

function encodeCondition(row: ConditionRow): Record<string, JsonValue> {
  const result: Record<string, JsonValue> = {};
  if (row.field) result.field = row.field;
  if (row.operator) result.operator = row.operator;
  if (row.value !== undefined) {
    const numeric = row.field?.startsWith('summary.');
    if (numeric && /^-?[0-9]+$/.test(row.value.trim())) result.value = Number(row.value);
    else if (row.value !== '' || !numeric) result.value = row.value;
  }
  return result;
}

function encodeConfig(data: FlowData, properties: Record<string, ParamField>): Record<string, JsonValue> {
  if (data.kind === 'condition') {
    const group = data.conditionGroup;
    return group ? { mode: group.mode, conditions: group.conditions.map(encodeCondition) } : encodeCondition(data.config ?? {});
  }
  const config: Record<string, JsonValue> = {};
  for (const [key, value] of Object.entries(data.config ?? {})) {
    if (value === '' || !(key in properties)) continue;
    if (properties[key].type === 'array' && splitList(value).length === 0) continue;
    config[key] = toConfigValue(properties[key], value);
  }
  return config;
}

function decodeConfig(node: ApiGraph['nodes'][number]): Pick<FlowData, 'config' | 'conditionGroup'> {
  const { mode, conditions } = node.config ?? {};
  if (node.type === 'condition' && (mode === 'AND' || mode === 'OR') && Array.isArray(conditions)) {
    const rows = conditions.map(rule => {
      if (!rule || typeof rule !== 'object' || Array.isArray(rule)) throw new Error('Некорректная проверка условия.');
      return Object.fromEntries(Object.entries(rule).map(([key, value]) => [key, String(value)]));
    });
    return { conditionGroup: { mode, conditions: rows } };
  }
  return { config: Object.fromEntries(Object.entries(node.config ?? {}).filter(([, v]) => v !== null)
    .map(([k, v]) => [k, Array.isArray(v) ? v.join('\n') : String(v)])) };
}

export function toApiGraph(nodes: FlowNode[], edges: Edge[], types: NodeTypeDefinition[]): ApiGraph {
  return {
    nodes: nodes.map(n => {
      const properties = types.find(t => t.kind === n.data.kind)?.params_schema.properties ?? {};
      const config = encodeConfig(n.data, properties);
      return { id: n.id, type: n.data.kind, config, position: { x: Math.round(n.position.x), y: Math.round(n.position.y) } };
    }),
    edges: edges.map(e => {
      const fromType = types.find(t => t.kind === nodes.find(n => n.id === e.source)?.data.kind);
      const toType = types.find(t => t.kind === nodes.find(n => n.id === e.target)?.data.kind);
      return {
        id: e.id,
        source: e.source,
        source_port: e.sourceHandle ?? fromType?.outputs[0]?.id ?? 'out',
        target: e.target,
        target_port: e.targetHandle ?? toType?.inputs[0]?.id ?? 'in',
      };
    }),
  };
}

export function fromApiGraph(graph: ApiGraph, types: NodeTypeDefinition[], labels: Record<string, string> = {}): { nodes: FlowNode[]; edges: Edge[] } {
  return {
    nodes: graph.nodes.map(n => ({
      id: n.id,
      type: 'flowNode',
      position: n.position,
      data: {
        kind: n.type,
        label: labels[n.id] ?? types.find(t => t.kind === n.type)?.label ?? n.type,
        ...decodeConfig(n),
      },
    })),
    edges: graph.edges.map(e => ({
      id: e.id, source: e.source, sourceHandle: e.source_port, target: e.target, targetHandle: e.target_port,
      style: { stroke: '#a7a0b8', strokeWidth: 2 },
    })),
  };
}
