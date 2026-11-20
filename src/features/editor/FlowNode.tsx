import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Bot, Settings2, ShieldCheck } from 'lucide-react';
import type { FlowNode } from './types';
import { catalog } from './catalog';
export function CustomNode({ data, selected }: NodeProps<FlowNode>) {
  return (
    <div className={`flow-node ${selected ? 'selected ' : ''}${data.kind}`}>
      <Handle type="target" position={Position.Left} />
      <div className="node-icon">
        {data.kind === 'ai' ? (
          <Bot size={18} />
        ) : data.kind === 'condition' ? (
          <Settings2 size={18} />
        ) : (
          <ShieldCheck size={18} />
        )}
      </div>
      <div>
        <strong>{data.label}</strong>
        <small>{catalog.find((c) => c.kind === data.kind)?.description}</small>
      </div>
      {data.status && (
        <span className={`node-status ${data.status}`}>
          {data.status === 'done' ? '✓' : data.status === 'running' ? '…' : '!'}
        </span>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export const nodeTypes = { flowNode: CustomNode };

