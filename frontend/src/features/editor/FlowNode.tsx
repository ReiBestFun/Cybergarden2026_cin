import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Bell, Bot, GitBranch, Merge, Play, Settings2, ShieldCheck } from 'lucide-react';
import type { FlowNode } from './types';
import { useNodeTypesStore } from './nodeTypesStore';

const STATUS_ICON: Record<string, string> = { succeeded: '✓', running: '…', failed: '!', skipped: '–', pending: '·' };
const STATUS_TITLE: Record<string, string> = { succeeded: 'Выполнено', running: 'Выполняется', failed: 'Ошибка', skipped: 'Пропущено', pending: 'Ожидает' };

function NodeIcon({ kind }: { kind: string }) {
  if (kind.endsWith('_trigger')) return <Play size={18} />;
  if (kind === 'repository') return <GitBranch size={18} />;
  if (kind === 'merge_findings') return <Merge size={18} />;
  if (kind === 'condition') return <Settings2 size={18} />;
  if (kind === 'ai_report') return <Bot size={18} />;
  if (kind.startsWith('notify_')) return <Bell size={18} />;
  return <ShieldCheck size={18} />;
}

export function CustomNode({ data, selected }: NodeProps<FlowNode>) {
  const definition = useNodeTypesStore(s => s.types.find(t => t.kind === data.kind));
  const inputs = definition?.inputs ?? [];
  const outputs = definition?.outputs ?? [];
  const position = (index: number, total: number) => `${((index + 1) / (total + 1)) * 100}%`;
  return <div className={`flow-node ${selected ? 'selected ' : ''}${data.kind}${data.status ? ` status-${data.status}` : ''}`}>
    {inputs.map((port, i) => <Handle key={`in-${port.id}`} id={port.id} type="target" position={Position.Left}
      style={{ top: position(i, inputs.length) }} title={`Вход: ${port.id}${port.type ? ` (${port.type})` : ''}`} />)}
    <div className="node-icon"><NodeIcon kind={data.kind} /></div>
    <div><strong>{data.label}</strong><small>{definition?.description ?? data.kind}</small></div>
    {data.status && <span className={`node-status ${data.status}`} title={STATUS_TITLE[data.status]}>{STATUS_ICON[data.status]}</span>}
    {outputs.map((port, i) => <Handle key={`out-${port.id}`} id={port.id} type="source" position={Position.Right}
      style={{ top: position(i, outputs.length) }} title={`Выход: ${port.id}${port.type ? ` (${port.type})` : ''}`} />)}
    {outputs.length > 1 && <div className="node-port-labels" aria-hidden="true">
      {outputs.map((port, i) => <span key={port.id} style={{ position: 'absolute', right: 12, top: position(i, outputs.length), transform: 'translateY(-50%)', fontSize: 9, fontWeight: 700, color: port.id === 'true' ? '#32805b' : port.id === 'false' ? '#b45b44' : '#7d7169', pointerEvents: 'none' }}>{port.id}</span>)}
    </div>}
  </div>;
}
export const nodeTypes = { flowNode: CustomNode };
