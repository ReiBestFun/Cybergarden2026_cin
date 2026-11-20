import { Plus } from 'lucide-react';
import { useEditorStore } from './store';
import { useNodeTypesStore } from './nodeTypesStore';

export function NodePalette() {
  const addNode = useEditorStore(s => s.addNode);
  const { types, source } = useNodeTypesStore();
  return (
    <aside className="node-library">
      <h3>Библиотека нод</h3>
      <p className="muted">
        Перетащите блок на поле
        {source === 'mock' && <><br /><span className="tag warn">Офлайн-каталог: API недоступен</span></>}
      </p>
      {[...new Set(types.map((c) => c.category))].map((cat) => (
        <section key={cat}>
          <h4>{cat}</h4>
          {types
            .filter((c) => c.category === cat)
            .map((item) => (
              <div
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
              </div>
            ))}
        </section>
      ))}
    </aside>
  );
}
