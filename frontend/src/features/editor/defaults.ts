import type { Edge } from '@xyflow/react';
import type { FlowNode } from './types';
import type { Frequency, Schedule, ScheduleSettings } from '../../shared/api/workflows';

export const DEFAULT_REPOSITORY = 'https://github.com/TBagirov/training';
export const DEFAULT_NAME = 'Security check workflow';
export const TRAINING_CI_SHA = 'fe1fcbe212669783480666b9b60147c8a8c6eef4';

const node = (id: string, kind: string, label: string, x: number, y: number): FlowNode =>
  ({ id, type: 'flowNode', position: { x, y }, data: { kind, label, config: {} } });
const edge = (id: string, source: string, sourceHandle: string, target: string, targetHandle: string): Edge =>
  ({ id, source, sourceHandle, target, targetHandle, style: { stroke: '#a7a0b8', strokeWidth: 2 } });

// The analysis chain is used by the explicit CI/CD preset.
const analysisNodes: FlowNode[] = [
  node('trigger', 'manual_trigger', 'Ручной запуск', 40, 200),
  node('repo', 'repository', 'Репозиторий', 320, 200),
  node('secrets', 'scan_secrets', 'Secrets', 620, 60),
  node('sca', 'scan_sca', 'SCA', 620, 200),
  node('sast', 'scan_sast', 'SAST', 620, 340),
  node('merge', 'merge_findings', 'Объединение находок', 920, 200),
];

const analysisEdges: Edge[] = [
  edge('e-trigger-repo', 'trigger', 'params', 'repo', 'params'),
  edge('e-repo-secrets', 'repo', 'snapshot', 'secrets', 'snapshot'),
  edge('e-repo-sca', 'repo', 'snapshot', 'sca', 'snapshot'),
  edge('e-repo-sast', 'repo', 'snapshot', 'sast', 'snapshot'),
  edge('e-secrets-merge', 'secrets', 'result', 'merge', 'results'),
  edge('e-sca-merge', 'sca', 'result', 'merge', 'results'),
  edge('e-sast-merge', 'sast', 'result', 'merge', 'results'),
];

export const ciPreset = {
  name: 'CI/CD · Учебный репозиторий',
  project: DEFAULT_REPOSITORY,
  nodes: [
    { ...node('trigger', 'ci_trigger', 'GitHub Actions', 40, 200), data: { kind: 'ci_trigger', label: 'GitHub Actions', config: { branch: 'main' } } },
    ...analysisNodes.slice(1),
    node('gate', 'ci_gate', 'Порог CI', 1210, 200),
  ],
  edges: [...analysisEdges, edge('e-merge-gate', 'merge', 'bundle', 'gate', 'bundle')],
};

// Local, editable schedule settings; all fields are kept so switching frequency loses nothing.
export type ScheduleDraft = {
  enabled: boolean; frequency: Frequency; time: string; weekdays: number[]; day_of_month: number; cron: string; timezone: string;
};

export const browserTimeZone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
};

export const defaultSchedule = (timezone = browserTimeZone()): ScheduleDraft => ({
  enabled: true, frequency: 'daily', time: '09:00', weekdays: [0], day_of_month: 1, cron: '0 9 * * 1-5', timezone,
});

export function scheduleSettings(draft: ScheduleDraft): ScheduleSettings {
  const base = { frequency: draft.frequency, timezone: draft.timezone };
  if (draft.frequency === 'cron') return { ...base, cron: draft.cron };
  if (draft.frequency === 'weekly') return { ...base, time: draft.time, weekdays: draft.weekdays };
  if (draft.frequency === 'monthly') return { ...base, time: draft.time, day_of_month: draft.day_of_month };
  return { ...base, time: draft.time };
}

export function hasScheduleFields(draft: ScheduleDraft): boolean {
  if (draft.frequency === 'cron') return !!draft.cron.trim();
  if (!draft.time) return false;
  if (draft.frequency === 'weekly') return draft.weekdays.length > 0;
  if (draft.frequency === 'monthly') return draft.day_of_month > 0;
  return true;
}

export function scheduleFromServer(s: Schedule, fallback: ScheduleDraft): ScheduleDraft {
  return {
    enabled: s.enabled,
    frequency: s.frequency ?? 'daily',
    time: s.time ?? fallback.time,
    weekdays: s.weekdays?.length ? s.weekdays : fallback.weekdays,
    day_of_month: s.day_of_month ?? fallback.day_of_month,
    cron: s.cron ?? fallback.cron,
    timezone: s.timezone ?? fallback.timezone,
  };
}

let lastRunId: string | null = null;
export const getLastRunId = () => lastRunId;
export const setLastRunId = (id: string) => { lastRunId = id; };
