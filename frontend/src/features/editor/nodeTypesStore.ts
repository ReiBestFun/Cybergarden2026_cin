import { create } from 'zustand';
import { fetchNodeTypes, mockNodeTypes, type NodeTypeDefinition } from '../../shared/api/nodeTypes';

type CatalogState = {
  types: NodeTypeDefinition[];
  loading: boolean;
  source: 'mock' | 'api';
  refresh: () => Promise<void>;
};
export const useNodeTypesStore = create<CatalogState>((set) => ({
  types: mockNodeTypes,
  loading: false,
  source: 'mock',
  refresh: async () => {
    set({loading:true});
    // API result is distinguished from mock via fetch status in a later API contract.
    try { const types = await fetchNodeTypes(); set({types, source: types === mockNodeTypes ? 'mock' : 'api'}); }
    finally { set({loading:false}); }
  },
}));
