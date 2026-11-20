import { create } from 'zustand';
import { applyNodeChanges, applyEdgeChanges, addEdge, type NodeChange, type EdgeChange, type Connection, type Edge } from '@xyflow/react';
import type { FlowNode, NodeKind } from './types';
import { catalog } from './catalog';
import { getSaved } from './defaults';

type EditorState = {
  nodes: FlowNode[]; edges: Edge[]; selected: string | null; project: string;
  setNodes: (update: FlowNode[] | ((nodes: FlowNode[]) => FlowNode[])) => void;
  setEdges: (update: Edge[] | ((edges: Edge[]) => Edge[])) => void;
  onNodesChange: (changes: NodeChange<FlowNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<Edge>[]) => void;
  onConnect: (connection: Connection) => void;
  setSelected: (id: string | null) => void;
  setProject: (value: string) => void;
  addNode: (kind: NodeKind, position?: { x: number; y: number }) => void;
  updateConfig: (key: string, value: string) => void;
  renameSelected: (label: string) => void;
  removeSelected: () => void;
};
const saved = getSaved();
export const useEditorStore = create<EditorState>((set, get) => ({
  nodes: saved.nodes, edges: saved.edges, selected: null,
  project: saved.project ?? 'https://github.com/TBagirov/training',
  setNodes: (update) => set((state) => ({ nodes: typeof update === 'function' ? update(state.nodes) : update })),
  setEdges: (update) => set((state) => ({ edges: typeof update === 'function' ? update(state.edges) : update })),
  onNodesChange: (changes) => set((state) => ({ nodes: applyNodeChanges(changes, state.nodes) })),
  onEdgesChange: (changes) => set((state) => ({ edges: applyEdgeChanges(changes, state.edges) })),
  onConnect: (connection) => set((state) => ({ edges: addEdge({ ...connection, animated: false, style: { stroke: '#a7a0b8', strokeWidth: 2 } }, state.edges) })),
  setSelected: (selected) => set({ selected }),
  setProject: (project) => set({ project }),
  addNode: (kind, position) => {
    const item = catalog.find((x) => x.kind === kind);
    if (!item) return;
    const id = crypto.randomUUID();
    set((state) => ({ selected: id, nodes: [...state.nodes, { id, type: 'flowNode', position: position ?? { x: 160 + state.nodes.length * 45, y: 180 + state.nodes.length * 35 }, data: { kind, label: item.label, config: {} } }] }));
  },
  updateConfig: (key, value) => set((state) => ({ nodes: state.nodes.map((n) => n.id === state.selected ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } } : n) })),
  renameSelected: (label) => set((state) => ({ nodes: state.nodes.map((n) => n.id === state.selected ? { ...n, data: { ...n.data, label } } : n) })),
  removeSelected: () => set((state) => ({ nodes: state.nodes.filter((n) => n.id !== state.selected), edges: state.edges.filter((e) => e.source !== state.selected && e.target !== state.selected), selected: null })),
}));
