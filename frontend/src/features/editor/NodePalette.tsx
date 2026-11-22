import { useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';

export function NodePalette() {
  const addNode = useEditorStore(s => s.addNode);
  const { types, source } = useNodeTypesStore();
  const [query, setQuery] = useState('');
  const search = query.trim().toLocaleLowerCase('ru');
  const filtered = types.filter(item => [item.label, item.kind, item.category].some(value => value.toLocaleLowerCase('ru').includes(search)));
  return (
    <aside className="node-library">
      <h3>Библиотека нод</h3>
      <p className="muted">
        Нажмите на блок или перетащите его на поле.
        {source === 'mock' && <><br /><span className="tag warn">Офлайн-каталог: API недоступен</span></>}
      </p>
      <label className="palette-search"><Search size={15} /><input aria-label="Найти блок" type="search" placeholder="Найти блок" value={query} onChange={event => setQuery(event.target.value)} /></label>
      {!filtered.length && <p className="muted" role="status">Блоки не найдены.</p>}
      {[...new Set(filtered.map((c) => c.category))].map((cat) => (
        <section key={cat}>
          <h4>{cat}</h4>
          {filtered
            .filter((c) => c.category === cat)
            .map((item) => (
              <button type="button"
                key={item.kind}
                draggable
                onDragStart={(e) => e.dataTransfer.setData('application/cyberflow-node', item.kind)}
                onClick={() => addNode(item.kind)}
                className="library-node"
              >
                <Plus size={15} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
              </button>
            ))}
        </section>
      ))}
    </aside>
  );
}
