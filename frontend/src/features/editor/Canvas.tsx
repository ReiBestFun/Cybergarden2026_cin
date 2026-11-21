import { useCallback } from 'react';
import type React from 'react';
import { ReactFlow, Background, Controls, MiniMap, useReactFlow, type Connection } from '@xyflow/react';
import { useNodeTypesStore } from './nodeTypesStore';
import { nodeTypes } from './FlowNode';
import { useEditorStore } from './store';
import { validateConnection } from './validateConnection';
import type { NodeKind } from './types';

export function Canvas() {
  const { nodes, edges, onNodesChange, onEdgesChange, onConnect, setSelected, addNode } = useEditorStore();
  const knownKinds = useNodeTypesStore(s => s.types);
  const { screenToFlowPosition } = useReactFlow();
  const isValidConnection = useCallback((connection: Connection) =>
    validateConnection(connection, nodes, edges, knownKinds), [nodes, edges, knownKinds]);
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const kind = e.dataTransfer.getData('application/cyberflow-node');
    if (!knownKinds.some(t => t.kind === kind)) return;
    addNode(
      kind as NodeKind,
      screenToFlowPosition({ x: e.clientX, y: e.clientY })
    );
  }, [addNode, knownKinds, screenToFlowPosition]);
  return <main className="canvas" onDrop={onDrop} onDragOver={e => e.preventDefault()}>
    <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onConnect={onConnect} 
      isValidConnection={(edgeOrConnection) =>
        isValidConnection({
          source: edgeOrConnection.source,
          target: edgeOrConnection.target,
          sourceHandle: edgeOrConnection.sourceHandle ?? null,
          targetHandle: edgeOrConnection.targetHandle ?? null,
        })
      } 
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => setSelected(n.id)} onPaneClick={() => setSelected(null)} fitView>
      <Background color="#dfd4c8" gap={22} size={1}/><Controls/><MiniMap pannable zoomable nodeColor="#cbbeb5"/>
    </ReactFlow>
    <div className="canvas-hint">Соединяйте порты одного типа. Запуск выполняется на бэкенде, статусы нод обновляются по ходу.</div>
  </main>;
}
