import { create } from 'zustand';
import { fetchNodeTypes, mockNodeTypes, type NodeTypeDefinition } from '../../shared/api/nodeTypes';
import { errorMessage } from '../../shared/api/client';

type CatalogState = {
  types: NodeTypeDefinition[];
  loading: boolean;
  source: 'mock' | 'api';
  error: string | null;
  refresh: () => Promise<void>;
};

export const useNodeTypesStore = create<CatalogState>((set) => ({
  types: mockNodeTypes,
  loading: false,
  source: 'mock',
  error: null,
  refresh: async () => {
    set({ loading: true });
    try {
      const types = await fetchNodeTypes();
      set({ types, source: 'api', error: null });
    } catch (error) {
      console.warn('GET /api/check-types failed, using offline catalog:', error);
      set({ types: mockNodeTypes, source: 'mock', error: errorMessage(error) });
    } finally {
      set({ loading: false });
    }
  },
}));
