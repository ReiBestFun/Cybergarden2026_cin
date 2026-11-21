import { create } from 'zustand';
import { applyNodeChanges, applyEdgeChanges, addEdge, type NodeChange, type EdgeChange, type Connection, type Edge } from '@xyflow/react';
import type { FlowNode, NodeKind } from './types';
import { useNodeTypesStore } from './nodeTypesStore';
import {
  getSaved, saveDraft, setLastRunId, initialNodes, initialEdges, DEFAULT_NAME,
  defaultSchedule, scheduleFromServer, scheduleSettings, type ScheduleDraft,
} from './defaults';
import { validateConnection } from './validateConnection';
import { fromApiGraph, toApiGraph } from './graph';
import { ApiError, errorMessage } from '../../shared/api/client';
import {
  createWorkflow, ensureProject, getRun, getSchedule, getWorkflow, isFinished, setSchedule, startRun, updateWorkflow, validateWorkflow,
  type RunDetail, type Schedule,
} from '../../shared/api/workflows';

export type Notice = { kind: 'error' | 'info' | 'success'; text: string } | null;

type EditorState = {
  nodes: FlowNode[]; edges: Edge[]; selected: string | null;
  project: string; name: string; projectId: string | null; workflowId: string | null;
  schedule: ScheduleDraft; serverSchedule: Schedule | null;
  busy: 'save' | 'run' | 'load' | null; notice: Notice; run: RunDetail | null;
  setNodes: (update: FlowNode[] | ((nodes: FlowNode[]) => FlowNode[])) => void;
  setEdges: (update: Edge[] | ((edges: Edge[]) => Edge[])) => void;
  onNodesChange: (changes: NodeChange<FlowNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<Edge>[]) => void;
  onConnect: (connection: Connection) => void;
  setSelected: (id: string | null) => void;
  setProject: (value: string) => void;
  setName: (value: string) => void;
  setSchedule: (value: Partial<ScheduleDraft>) => void;
  addNode: (kind: NodeKind, position?: { x: number; y: number }) => void;
  updateConfig: (key: string, value: string) => void;
  renameSelected: (label: string) => void;
  removeSelected: () => void;
  setNotice: (notice: Notice) => void;
  newWorkflow: () => void;
  loadWorkflow: (id: string) => Promise<void>;
  save: () => Promise<string | null>;
  runWorkflow: () => Promise<void>;
};

const saved = getSaved();
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

export const useEditorStore = create<EditorState>((set, get) => {
  const persist = () => {
    const s = get();
    saveDraft({ nodes: s.nodes, edges: s.edges, project: s.project, name: s.name, projectId: s.projectId, workflowId: s.workflowId, schedule: s.schedule });
  };
  // Shows an API error and, when it names a node, selects that node.
  const fail = (error: unknown) => {
    const nodeId = error instanceof ApiError ? error.body.node_id : null;
    set({ notice: { kind: 'error', text: errorMessage(error) }, ...(nodeId && get().nodes.some(n => n.id === nodeId) ? { selected: nodeId } : {}) });
  };
  const clearStatuses = () => set(s => ({ nodes: s.nodes.map(n => ({ ...n, data: { ...n.data, status: undefined } })) }));

  return {
    nodes: saved.nodes, edges: saved.edges, selected: null,
    project: saved.project, name: saved.name, projectId: saved.projectId, workflowId: saved.workflowId,
    schedule: { ...defaultSchedule(), ...saved.schedule }, serverSchedule: null,
    busy: null, notice: null, run: null,
    setNodes: (update) => set((state) => ({ nodes: typeof update === 'function' ? update(state.nodes) : update })),
    setEdges: (update) => set((state) => ({ edges: typeof update === 'function' ? update(state.edges) : update })),
    onNodesChange: (changes) => set((state) => ({ nodes: applyNodeChanges(changes, state.nodes) })),
    onEdgesChange: (changes) => set((state) => ({ edges: applyEdgeChanges(changes, state.edges) })),
    onConnect: (connection) => set((state) => {
      const types = useNodeTypesStore.getState().types;
      if (!validateConnection(connection, state.nodes, state.edges, types)) return state;
      return { edges: addEdge({ ...connection, animated: false, style: { stroke: '#a7a0b8', strokeWidth: 2 } }, state.edges) };
    }),
    setSelected: (selected) => set({ selected }),
    setProject: (project) => set({ project }),
    setName: (name) => set({ name }),
    setSchedule: (patch) => set(s => ({ schedule: { ...s.schedule, ...patch } })),
    addNode: (kind, position) => {
      const item = useNodeTypesStore.getState().types.find((x) => x.kind === kind);
      if (!item) return;
      const id = `${kind}-${crypto.randomUUID().slice(0, 8)}`;
      const config = Object.fromEntries(Object.entries(item.params_schema.properties ?? {})
        .filter(([, field]) => field.default !== undefined).map(([key, field]) => [key, String(field.default)]));
      set((state) => ({ selected: id, nodes: [...state.nodes, { id, type: 'flowNode', position: position ?? { x: 160 + state.nodes.length * 45, y: 180 + state.nodes.length * 35 }, data: { kind, label: item.label, config } }] }));
    },
    updateConfig: (key, value) => set((state) => ({ nodes: state.nodes.map((n) => n.id === state.selected ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } } : n) })),
    renameSelected: (label) => set((state) => ({ nodes: state.nodes.map((n) => n.id === state.selected ? { ...n, data: { ...n.data, label } } : n) })),
    removeSelected: () => set((state) => ({ nodes: state.nodes.filter((n) => n.id !== state.selected), edges: state.edges.filter((e) => e.source !== state.selected && e.target !== state.selected), selected: null })),
    setNotice: (notice) => set({ notice }),

    newWorkflow: () => {
      set({ nodes: initialNodes, edges: initialEdges, name: DEFAULT_NAME, workflowId: null, selected: null, run: null, notice: null, schedule: defaultSchedule(), serverSchedule: null });
      persist();
    },

    loadWorkflow: async (id) => {
      set({ busy: 'load', notice: null });
      try {
        const workflow = await getWorkflow(id);
        const types = useNodeTypesStore.getState().types;
        const labels = get().workflowId === id ? Object.fromEntries(get().nodes.map(n => [n.id, n.data.label])) : {};
        const { nodes, edges } = fromApiGraph(workflow.graph, types, labels);
        const serverSchedule = await getSchedule(workflow.id).catch(() => null);
        const configured = serverSchedule && (serverSchedule.time || serverSchedule.cron);
        set({
          nodes, edges, name: workflow.name, workflowId: workflow.id, projectId: workflow.project_id, selected: null, run: null,
          serverSchedule, schedule: configured ? scheduleFromServer(serverSchedule, defaultSchedule()) : defaultSchedule(),
        });
        persist();
      } catch (error) {
        fail(error);
      } finally {
        set({ busy: null });
      }
    },

    save: async () => {
      const s = get();
      set({ busy: s.busy ?? 'save', notice: null });
      try {
        const graph = toApiGraph(s.nodes, s.edges, useNodeTypesStore.getState().types);
        const project = await ensureProject(s.project, s.projectId);
        let workflowId = project.id === s.projectId ? s.workflowId : null;
        if (workflowId) {
          try {
            await updateWorkflow(workflowId, s.name, graph);
          } catch (error) {
            if (!(error instanceof ApiError && error.status === 404)) throw error;
            workflowId = null;
          }
        }
        if (!workflowId) workflowId = (await createWorkflow(project.id, s.name, graph)).id;
        set({ projectId: project.id, workflowId });
        const scheduled = s.nodes.some(n => n.data.kind === 'schedule_trigger');
        if (scheduled || s.workflowId) {
          const serverSchedule = await setSchedule(workflowId, scheduled && s.schedule.enabled
            ? { enabled: true, ...scheduleSettings(s.schedule) }
            : { enabled: false });
          set({ serverSchedule });
        }
        persist();
        if (get().busy === 'save') set({ notice: { kind: 'success', text: 'Сценарий сохранён на сервере.' } });
        return workflowId;
      } catch (error) {
        fail(error);
        return null;
      } finally {
        if (get().busy === 'save') set({ busy: null });
      }
    },

    runWorkflow: async () => {
      if (get().busy) return;
      set({ busy: 'run', notice: null, run: null });
      clearStatuses();
      try {
        const workflowId = await get().save();
        if (!workflowId) return;
        const check = await validateWorkflow(workflowId);
        if (!check.valid) throw new ApiError(422, check.issues[0]);
        const { id } = await startRun(workflowId);
        setLastRunId(id);
        set({ notice: { kind: 'info', text: 'Запуск в очереди. Ожидаем воркер…' } });
        for (;;) {
          const run = await getRun(id);
          const statuses = Object.fromEntries(run.nodes.map(n => [n.node_id, n.status]));
          set(state => ({ run, nodes: state.nodes.map(n => ({ ...n, data: { ...n.data, status: statuses[n.id] } })) }));
          if (run.status === 'running') set({ notice: { kind: 'info', text: 'Выполняется…' } });
          if (isFinished(run.status)) {
            const total = run.summary?.total ?? 0;
            set({ notice: run.status === 'failed'
              ? { kind: 'error', text: `Запуск завершился ошибкой: ${run.error?.message ?? 'неизвестная ошибка'}` }
              : { kind: 'success', text: `Запуск завершён (${run.status === 'partial' ? 'частичное покрытие' : 'успешно'}). Находок: ${total}.` } });
            break;
          }
          await sleep(1000);
        }
      } catch (error) {
        fail(error);
      } finally {
        set({ busy: null });
      }
    },
  };
});
