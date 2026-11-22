import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('editing a saved group preserves compatible comparisons and omits a cleared pattern', async t => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    server: { middlewareMode: true, hmr: false }, optimizeDeps: { noDiscovery: true },
  });
  t.after(() => server.close());
  const { changeConditionField } = await server.ssrLoadModule('/src/features/editor/conditionFields.ts');
  const { fromApiGraph, toApiGraph } = await server.ssrLoadModule('/src/features/editor/graph.ts');
  const input = { nodes: [{ id: 'if', type: 'condition', position: { x: 0, y: 0 }, config: {
    mode: 'OR', conditions: [
      { field: 'summary.high_count', operator: 'gte', value: 3 },
      { field: 'coverage.status', operator: 'matches', value: '^partial$' },
    ],
  } }], edges: [] };
  const { nodes, edges } = fromApiGraph(input, []);
  assert.deepEqual(toApiGraph(nodes, edges, []), input);
  const rows = nodes[0].data.conditionGroup.conditions;
  rows[0] = changeConditionField(rows[0], 'summary.critical_count');
  rows[1].value = '';
  assert.deepEqual(toApiGraph(nodes, edges, []).nodes[0].config.conditions, [
    { field: 'summary.critical_count', operator: 'gte', value: 3 },
    { field: 'coverage.status', operator: 'matches' },
  ]);
});
