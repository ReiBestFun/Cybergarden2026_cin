import { useCallback, useEffect, useMemo, useRef } from 'react';
import type React from 'react';
import { ReactFlow, Background, Controls, MiniMap, useNodesInitialized, useReactFlow, type Connection } from '@xyflow/react';
import { useNodeTypesStore } from './nodeTypesStore';
import { nodeTypes } from './FlowNode';
import { useEditorStore } from './store';
import { validateConnection } from './validateConnection';
import type { NodeKind } from './types';

export function Canvas({ editable = true }: { editable?: boolean }) {
  const { nodes, edges, run, canvasRevision, onNodesChange, onEdgesChange, onConnect, setSelected, addNode } = useEditorStore();
  const knownKinds = useNodeTypesStore(s => s.types);
  const { screenToFlowPosition, fitView } = useReactFlow();
  const nodesInitialized = useNodesInitialized();
  const fittedRevision = useRef<number | null>(null);
  useEffect(() => {
    if (!nodesInitialized || canvasRevision === fittedRevision.current) return;
    fittedRevision.current = canvasRevision;
    void fitView({ padding: 0.2, maxZoom: 1 });
  }, [canvasRevision, fitView, nodesInitialized]);
  const isValidConnection = useCallback((connection: Connection) =>
    validateConnection(connection, nodes, edges, knownKinds), [nodes, edges, knownKinds]);
  const displayedEdges = useMemo(() => {
    if (!run) return edges;
    const original = new Map(run.graph_snapshot.edges.map(edge => [edge.id, edge]));
    const executions = new Map(run.nodes.map(node => [node.node_id, node]));
    return edges.map(edge => {
      const saved = original.get(edge.id);
      if (!saved || saved.source !== edge.source || saved.target !== edge.target ||
          (edge.sourceHandle && saved.source_port !== edge.sourceHandle) ||
          (edge.targetHandle && saved.target_port !== edge.targetHandle)) return edge;
      const source = executions.get(edge.source);
      if (!source || source.status === 'pending' || source.status === 'running') return edge;
      const active = source.active_output_ports.includes(saved.source_port);
      const [label, color, background] = source.status === 'failed' ? ['Ошибка', '#902c27', '#fde8e3']
        : active ? ['Данные', '#28624b', '#e5f3eb'] : ['Пропущено', '#635b70', '#eeeaf2'];
      return { ...edge, label, labelStyle: { fill: color, fontWeight: 700, fontSize: 11 },
        labelBgStyle: { fill: background, fillOpacity: 1 }, labelBgPadding: [5, 3] as [number, number],
        labelBgBorderRadius: 4, ariaLabel: `${edge.source} → ${edge.target}: ${label}` };
    });
  }, [edges, run]);
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const kind = e.dataTransfer.getData('application/cyberflow-node');
    if (!knownKinds.some(t => t.kind === kind)) return;
    addNode(
      kind as NodeKind,
      screenToFlowPosition({ x: e.clientX, y: e.clientY })
    );
  }, [addNode, knownKinds, screenToFlowPosition]);
  return <main className="canvas" onDrop={editable ? onDrop : undefined} onDragOver={editable ? e => e.preventDefault() : undefined}>
    <ReactFlow nodes={nodes} edges={displayedEdges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onConnect={editable ? onConnect : undefined}
      nodesDraggable={editable} nodesConnectable={editable} edgesReconnectable={editable}
      deleteKeyCode={editable ? 'Backspace' : null}
      isValidConnection={(edgeOrConnection) =>
        isValidConnection({
          source: edgeOrConnection.source,
          target: edgeOrConnection.target,
          sourceHandle: edgeOrConnection.sourceHandle ?? null,
          targetHandle: edgeOrConnection.targetHandle ?? null,
        })
      } 
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => setSelected(n.id)} onPaneClick={() => setSelected(null)}>
      <Background color="#dfd4c8" gap={22} size={1}/><Controls/><MiniMap pannable zoomable nodeColor="#cbbeb5"/>
    </ReactFlow>
    {editable && <div className="canvas-hint">Соединяйте порты одного типа. Запуск выполняется на бэкенде, статусы нод обновляются по ходу.</div>}
  </main>;
}
