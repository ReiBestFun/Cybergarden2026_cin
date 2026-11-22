import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('findings request applies the category on the server and can reach later pages', async t => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    server: { middlewareMode: true, ws: false }, optimizeDeps: { noDiscovery: true },
  });
  t.after(() => server.close());
  const { listFindings } = await server.ssrLoadModule('/src/shared/api/workflows.ts');
  const findings = Array.from({ length: 201 }, (_, i) => ({ id: i, category: i === 200 ? 'secrets' : 'sast' }));
  t.mock.method(globalThis, 'fetch', async url => {
    const { pathname, searchParams } = new URL(url, 'http://localhost');
    assert.equal(pathname, '/api/runs/demo/findings');
    const category = searchParams.get('category');
    const rows = category === null ? findings : findings.filter(row => row.category === category);
    const offset = Number(searchParams.get('offset'));
    const limit = Number(searchParams.get('limit'));
    return Response.json({ items: rows.slice(offset, offset + limit), total: rows.length, offset, limit });
  });

  const last = await listFindings('demo', '', 200);
  assert.equal(last.total, 201);
  assert.deepEqual(last.items, [findings[200]]);
  const secrets = await listFindings('demo', 'secrets', 0);
  assert.equal(secrets.total, 1);
  assert.deepEqual(secrets.items, [findings[200]]);
});
