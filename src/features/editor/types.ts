import type { Node } from '@xyflow/react';
export type NodeKind = 'manual' | 'schedule' | 'secrets' | 'sca' | 'sast' | 'ai' | 'condition' | 'telegram';

export type FlowData = {
  label: string;
  kind: NodeKind;
  status?: string;
  config?: Record<string, string>;
};

export type FlowNode = Node<FlowData>;

