import type { Node } from '@xyflow/react';
import type { NodeStatus } from '../../shared/api/workflows';

// Backend node type from GET /api/check-types (manual_trigger, scan_secrets, ...).
export type NodeKind = string;

export type ConditionRow = Record<string, string>;
export type ConditionGroup = { mode: 'AND' | 'OR'; conditions: ConditionRow[] };

export type FlowData = {
  label: string;
  kind: NodeKind;
  status?: NodeStatus;
  config?: Record<string, string>;
  conditionGroup?: ConditionGroup;
};

export type FlowNode = Node<FlowData>;
