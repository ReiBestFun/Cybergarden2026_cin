import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Bot, Settings2, ShieldCheck } from 'lucide-react';
import type { FlowNode } from './types';
import { useNodeTypesStore } from './nodeTypesStore';

export function CustomNode({ data, selected }: NodeProps<FlowNode>) {
  const definition = useNodeTypesStore(s => s.types.find(t => t.kind === data.kind));
  const inputs = definition?.inputs ?? [{ id: 'in' }];
  const outputs = definition?.outputs ?? [{ id: 'out' }];
  const position = (index: number, total: number) => `${((index + 1) / (total + 1)) * 100}%`;
  return <div className={`flow-node ${selected ? 'selected ' : ''}${data.kind}`}>
    {inputs.map((port, i) => <Handle key={`in-${port.id}`} id={port.id} type="target" position={Position.Left}
      style={{ top: position(i, inputs.length) }} title={`Вход: ${port.id}`} />)}
    <div className="node-icon">{data.kind === 'ai' ? <Bot size={18}/> : data.kind === 'condition' ? <Settings2 size={18}/> : <ShieldCheck size={18}/>}</div>
    <div><strong>{data.label}</strong><small>{definition?.description ?? data.kind}</small></div>
    {data.status && <span className={`node-status ${data.status}`}>{data.status === 'done' ? '✓' : data.status === 'running' ? '…' : '!'}</span>}
    {outputs.map((port, i) => <Handle key={`out-${port.id}`} id={port.id} type="source" position={Position.Right}
      style={{ top: position(i, outputs.length) }} title={`Выход: ${port.id}`} />)}
    {outputs.length > 1 && <div className="node-port-labels" aria-hidden="true">
      {outputs.map((port, i) => <span key={port.id} style={{ position: 'absolute', right: 12, top: position(i, outputs.length), transform: 'translateY(-50%)', fontSize: 9, fontWeight: 700, color: port.id === 'true' ? '#32805b' : port.id === 'false' ? '#b45b44' : '#7d7169', pointerEvents: 'none' }}>{port.id}</span>)}
    </div>}
  </div>;
}
export const nodeTypes = { flowNode: CustomNode };
