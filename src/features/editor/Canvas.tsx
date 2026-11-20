import { useCallback } from 'react';
import type React from 'react';
import { ReactFlow, Background, Controls, MiniMap, useReactFlow } from '@xyflow/react';
import { catalog } from './catalog';
import type { NodeKind } from './types';
import { nodeTypes } from './FlowNode';
import { useEditorStore } from './store';
export function Canvas(){
 const {nodes,edges,onNodesChange,onEdgesChange,onConnect,setSelected,addNode}=useEditorStore();
 const { screenToFlowPosition } = useReactFlow();
  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const kind = e.dataTransfer.getData('application/cyberflow-node') as NodeKind;
      if (!catalog.some((c) => c.kind === kind)) return;
      addNode(kind, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
    },
    [addNode, screenToFlowPosition]
  );


 return (
        <main className="canvas" onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => setSelected(n.id)}
            onPaneClick={() => setSelected(null)}
            fitView
          >
            <Background color="#dfd4c8" gap={22} size={1} />
            <Controls />
            <MiniMap pannable zoomable nodeColor="#cbbeb5" />
          </ReactFlow>
          <div className="canvas-hint">Демо: статусы имитируются. Реальные сканеры пока не подключены.</div>
        </main>
 );
}
