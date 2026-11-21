import { createContext, useContext } from 'react';
import type { Workspace } from '../shared/api/workspaces';

export const WorkspaceContext = createContext<{
  workspace: Workspace;
  workspaces: Workspace[];
  switchLocked: boolean;
  withWorkspaceLock: <T>(operation: () => Promise<T>) => Promise<T>;
} | null>(null);

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('Workspace context is missing.');
  return value;
}
