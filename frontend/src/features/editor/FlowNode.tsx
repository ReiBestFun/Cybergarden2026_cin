import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Bell, Bot, FileArchive, GitBranch, Mail, Merge, Play, Send, Settings2, ShieldCheck } from 'lucide-react';
import type { FlowNode } from './types';
import { useNodeTypesStore } from './nodeTypesStore';

const STATUS_ICON: Record<string, string> = { succeeded: '✓', running: '…', failed: '!', skipped: '–', pending: '·' };
const STATUS_TITLE: Record<string, string> = { succeeded: 'Выполнено', running: 'Выполняется', failed: 'Ошибка', skipped: 'Пропущено', pending: 'Ожидает' };

function NodeIcon({ kind }: { kind: string }) {
  if (kind.endsWith('_trigger')) return <Play size={18} />;
  if (kind === 'repository') return <GitBranch size={18} />;
  if (kind === 'local_archive') return <FileArchive size={18} />;
  if (kind === 'merge_findings') return <Merge size={18} />;
  if (kind === 'condition') return <Settings2 size={18} />;
  if (kind === 'ai_report') return <Bot size={18} />;
  if (kind === 'notify_email') return <Mail size={18} />;
  if (kind === 'notify_telegram') return <Send size={18} />;
  if (kind.startsWith('notify_')) return <Bell size={18} />;
  return <ShieldCheck size={18} />;
}

export function CustomNode({ data, selected }: NodeProps<FlowNode>) {
  const definition = useNodeTypesStore(s => s.types.find(t => t.kind === data.kind));
  const inputs = definition?.inputs ?? [];
  const outputs = definition?.outputs ?? [];
  const position = (index: number, total: number) => `${((index + 1) / (total + 1)) * 100}%`;
  const categoryColors: Record<string, [string, string]> = {
    'Запуск': ['#E9E4F5', '#A99ACF'],
    'Источники': ['#E2EDF8', '#91B5D7'],
    'Анализаторы': ['#F9E7D9', '#DBA77E'],
    'Логика': ['#F6E7EF', '#D5A0B9'],
    'Отчёты': ['#E4F1E6', '#9AC7A3'],
    'Уведомления': ['#F7EDCF', '#D7BF76'],
  };

  const [nodeBg, nodeBorder] =
    categoryColors[definition?.category ?? ''] ??
    ['#FFFFFF', '#DED1C4'];

  return (
    <div
      className={`flow-node ${selected ? 'selected ' : ''}${data.kind}${
        data.status ? ` status-${data.status}` : ''
      }`}
      style={{
        backgroundColor: nodeBg,
        borderColor:
          data.status === 'failed'
            ? '#D9534F'
            : data.status === 'succeeded'
            ? '#32805B'
            : data.status === 'running'
            ? '#D9A441'
            : nodeBorder,
      }}
    >
      {/* Входные порты (слева) */}
      {inputs.map((port, i) => (
        <Handle
          key={`in-${port.id}`}
          id={port.id}
          type="target"
          position={Position.Left}
          style={{ top: position(i, inputs.length) }}
          title={`Вход: ${port.id}${port.type ? ` (${port.type})` : ''}`}
        />
      ))}

      {/* Иконка ноды */}
      <div className="node-icon">
        <NodeIcon kind={data.kind} />
      </div>

      {/* Название ноды */}
      <div>
        <strong>{data.label}</strong>
      </div>

      {/* Статус ноды (если есть) */}
      {data.status && (
        <span
          className={`node-status ${data.status}`}
          title={STATUS_TITLE[data.status]}
        >
          {STATUS_ICON[data.status]}
        </span>
      )}

      {/* Выходные порты (справа) */}
      {outputs.map((port, i) => (
        <Handle
          key={`out-${port.id}`}
          id={port.id}
          type="source"
          position={Position.Right}
          style={{ top: position(i, outputs.length) }}
          title={`Выход: ${port.id}${port.type ? ` (${port.type})` : ''}`}
        />
      ))}

      {/* Подписи выходных портов (если выходов больше одного) */}
      {outputs.length > 1 && (
        <div className="node-port-labels" aria-hidden="true">
          {outputs.map((port, i) => (
            <span
              key={port.id}
              style={{
                position: 'absolute',
                right: 12,
                top: position(i, outputs.length),
                transform: 'translateY(-50%)',
                fontSize: 9,
                fontWeight: 700,
                color:
                  port.id === 'true'
                    ? '#32805b'
                    : port.id === 'false'
                    ? '#b45b44'
                    : '#7d7169',
                pointerEvents: 'none',
              }}
            >
              {port.id}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
export const nodeTypes = { flowNode: CustomNode };
