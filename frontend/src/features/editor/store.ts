import { create } from 'zustand';
import { applyNodeChanges, applyEdgeChanges, addEdge, type NodeChange, type EdgeChange, type Connection, type Edge } from '@xyflow/react';
import type { FlowNode, NodeKind } from './types';
import { useNodeTypesStore } from './nodeTypesStore';
import {
  browserTimeZone, setLastRunId, DEFAULT_NAME, DEFAULT_REPOSITORY,
  ciPreset, defaultSchedule, hasScheduleFields, scheduleFromServer, scheduleSettings, type ScheduleDraft,
} from './defaults';
import { validateConnection } from './validateConnection';
import { fromApiGraph, toApiGraph } from './graph';
import { ApiError, errorMessage } from '../../shared/api/client';
import { watchRun } from '../../shared/api/runEvents';
import {
  createWorkflow, ensureProject, getProject, getSchedule, getWorkflow, setSchedule, startCiTest, startRun, updateWorkflow, validateWorkflow,
  type GeneratedWorkflow, type Project, type RunDetail, type Schedule,
} from '../../shared/api/workflows';

export type Notice = { kind: 'error' | 'info' | 'success'; text: string } | null;

type EditorState = {
  nodes: FlowNode[]; edges: Edge[]; selected: string | null;
  canvasRevision: number;
  project: string; name: string; projectId: string | null; workflowId: string | null;
  schedule: ScheduleDraft; serverSchedule: Schedule | null;
  profileTimezone: string;
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
  setProfileTimezone: (timezone: string) => void;
  newWorkflow: (preset?: 'ci', target?: Project) => void;
  applyGenerated: (draft: GeneratedWorkflow) => void;
  loadWorkflow: (id: string) => Promise<void>;
  save: () => Promise<string | null>;
  runWorkflow: (commitSha?: string) => Promise<void>;
};

export const useEditorStore = create<EditorState>((set, get) => {
  let runUpdates: AbortController | undefined;
  let loadRequest = 0;
  // Shows an API error and, when it names a node, selects that node.
  const fail = (error: unknown) => {
    const nodeId = error instanceof ApiError ? error.body.node_id : null;
    set({ notice: { kind: 'error', text: errorMessage(error) }, ...(nodeId && get().nodes.some(n => n.id === nodeId) ? { selected: nodeId } : {}) });
  };
  const clearStatuses = () => set(s => ({ nodes: s.nodes.map(n => ({ ...n, data: { ...n.data, status: undefined } })) }));

  return {
    nodes: [], edges: [], selected: null, canvasRevision: 0,
    project: DEFAULT_REPOSITORY, name: DEFAULT_NAME, projectId: null, workflowId: null,
    schedule: defaultSchedule(), serverSchedule: null, profileTimezone: browserTimeZone(),
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
        .filter(([, field]) => field.default != null).map(([key, field]) => [key, String(field.default)]));
      set((state) => ({ selected: id, nodes: [...state.nodes, { id, type: 'flowNode', position: position ?? { x: 160 + state.nodes.length * 45, y: 180 + state.nodes.length * 35 }, data: { kind, label: item.label, config } }] }));
    },
    updateConfig: (key, value) => set((state) => ({ nodes: state.nodes.map((n) => n.id === state.selected ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } } : n) })),
    renameSelected: (label) => set((state) => ({ nodes: state.nodes.map((n) => n.id === state.selected ? { ...n, data: { ...n.data, label } } : n) })),
    removeSelected: () => set((state) => ({ nodes: state.nodes.filter((n) => n.id !== state.selected), edges: state.edges.filter((e) => e.source !== state.selected && e.target !== state.selected), selected: null })),
    setNotice: (notice) => set({ notice }),
    setProfileTimezone: (profileTimezone) => set(state => ({
      profileTimezone,
      schedule: state.nodes.length ? state.schedule : { ...state.schedule, timezone: profileTimezone },
    })),

    newWorkflow: (preset, target) => {
      runUpdates?.abort();
      runUpdates = undefined;
      set({ nodes: [], edges: [], name: DEFAULT_NAME, project: target?.repository_url ?? DEFAULT_REPOSITORY, projectId: target?.id ?? null, canvasRevision: get().canvasRevision + 1,
        workflowId: null, selected: null, run: null, notice: null, busy: null,
        schedule: defaultSchedule(get().profileTimezone), serverSchedule: null, ...(preset === 'ci' ? { ...ciPreset, projectId: null } : {}) });
    },

    applyGenerated: (draft) => {
      const state = get();
      if (state.busy) return;
      const graph = fromApiGraph(draft.graph, useNodeTypesStore.getState().types);
      const scheduleNode = graph.nodes.find(node => node.data.kind === 'schedule_trigger');
      const schedule = { ...defaultSchedule(state.profileTimezone), enabled: draft.issues.length === 0 };
      if (draft.schedule) {
        schedule.frequency = draft.schedule.frequency;
        schedule.time = draft.schedule.time ?? '';
        schedule.weekdays = draft.schedule.weekdays ?? [];
        schedule.day_of_month = draft.schedule.day_of_month ?? 0;
        schedule.cron = draft.schedule.cron ?? '';
        schedule.timezone = draft.schedule.timezone ?? schedule.timezone;
      }
      set({
        ...graph, name: draft.name, project: draft.repository_url ?? state.project,
        workflowId: null, run: null, serverSchedule: null, schedule, canvasRevision: state.canvasRevision + 1,
        selected: draft.issues[0]?.node_id ?? scheduleNode?.id ?? null,
        notice: draft.issues.length
          ? { kind: 'info', text: draft.issues.map(issue => issue.message).join(' ') + (scheduleNode ? ' После настройки включите расписание.' : '') }
          : { kind: 'success', text: 'Черновик создан. Проверьте настройки и сохраните сценарий.' },
      });
    },

    loadWorkflow: async (id) => {
      runUpdates?.abort();
      runUpdates = undefined;
      const request = ++loadRequest;
      const revision = get().canvasRevision;
      const isCurrent = () => request === loadRequest && revision === get().canvasRevision;
      set({ busy: 'load', notice: null });
      try {
        const workflow = await getWorkflow(id);
        const project = await getProject(workflow.project_id);
        const types = useNodeTypesStore.getState().types;
        const labels = get().workflowId === id ? Object.fromEntries(get().nodes.map(n => [n.id, n.data.label])) : {};
        const { nodes, edges } = fromApiGraph(workflow.graph, types, labels);
        const serverSchedule = await getSchedule(workflow.id).catch(() => null);
        if (!isCurrent()) return;
        const configured = serverSchedule && (serverSchedule.time || serverSchedule.cron);
        const fallback = get().workflowId === id ? get().schedule : defaultSchedule(get().profileTimezone);
        set({
          nodes, edges, name: workflow.name, workflowId: workflow.id, project: project.repository_url, projectId: workflow.project_id, selected: null, run: null,
          canvasRevision: revision + 1, busy: null,
          serverSchedule, schedule: configured ? scheduleFromServer(serverSchedule, defaultSchedule(get().profileTimezone))
            : { ...fallback, enabled: serverSchedule?.enabled ?? fallback.enabled },
        });
      } catch (error) {
        if (!isCurrent()) return;
        fail(error);
        set({ busy: null });
      }
    },

    save: async () => {
      const s = get();
      const isCurrent = () => s.canvasRevision === get().canvasRevision && s.busy !== 'load' && get().busy !== 'load';
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
        if (!isCurrent()) return null;
        set({ projectId: project.id, workflowId });
        const scheduled = s.nodes.some(n => n.data.kind === 'schedule_trigger');
        if (scheduled || s.workflowId) {
          const serverSchedule = await setSchedule(workflowId, scheduled && (s.schedule.enabled || hasScheduleFields(s.schedule))
            ? { enabled: s.schedule.enabled, ...scheduleSettings(s.schedule) } : { enabled: false });
          if (!isCurrent()) return null;
          set({ serverSchedule });
        }
        if (get().busy === 'save') set({ notice: { kind: 'success', text: 'Сценарий сохранён на сервере.' } });
        return workflowId;
      } catch (error) {
        if (isCurrent()) fail(error);
        return null;
      } finally {
        if (isCurrent() && get().busy === 'save') set({ busy: null });
      }
    },

    runWorkflow: async (commitSha) => {
      if (get().busy) return;
      const controller = new AbortController();
      runUpdates = controller;
      set({ busy: 'run', notice: null, run: null });
      clearStatuses();
      try {
        const workflowId = await get().save();
        if (!workflowId || controller.signal.aborted) return;
        const check = await validateWorkflow(workflowId);
        if (controller.signal.aborted) return;
        if (!check.valid) throw new ApiError(422, check.issues[0]);
        const { id } = commitSha ? await startCiTest(workflowId, commitSha) : await startRun(workflowId);
        if (controller.signal.aborted) return;
        setLastRunId(id);
        set({ notice: { kind: 'info', text: 'Запуск в очереди. Ожидаем воркер…' } });
        const run = await watchRun(id, controller.signal, run => {
          const statuses = Object.fromEntries(run.nodes.map(n => [n.node_id, n.status]));
          set(state => ({ run, nodes: state.nodes.map(n => ({ ...n, data: { ...n.data, status: statuses[n.id] } })) }));
          if (run.status === 'running') set({ notice: { kind: 'info', text: 'Выполняется…' } });
          if (run.status === 'queued') set({ notice: { kind: 'info', text: 'Запуск в очереди. Ожидаем воркер…' } });
        }, reconnecting => {
          if (reconnecting) set({ notice: { kind: 'info', text: 'Восстанавливаем поток событий…' } });
        });
        if (controller.signal.aborted) return;
        const total = run.summary?.total ?? 0;
        const decision = run.nodes.find(node => node.type === 'ci_gate')?.output_preview?.decision;
        if (commitSha && (decision === 'blocked' || decision === 'error')) {
          set({ notice: { kind: 'error', text: decision === 'blocked'
            ? `CI заблокирован по критичности. Находок: ${total}.`
            : `CI завершился с неполным покрытием. Находок: ${total}.` } });
        } else {
          set({ notice: run.status === 'failed'
            ? { kind: 'error', text: `Запуск завершился ошибкой: ${run.error?.message ?? 'неизвестная ошибка'}` }
            : { kind: 'success', text: `Запуск завершён (${run.status === 'partial' ? 'частичное покрытие' : 'успешно'}). Находок: ${total}.` } });
        }
      } catch (error) {
        if (!controller.signal.aborted) fail(error);
      } finally {
        if (runUpdates === controller) {
          runUpdates = undefined;
          set({ busy: null });
        }
      }
    },
  };
});
