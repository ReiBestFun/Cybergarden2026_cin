import { Plus } from 'lucide-react';
import { catalog } from './catalog';
import { useEditorStore } from './store';
export function NodePalette() {
 const addNode = useEditorStore(s => s.addNode);
 return (
        <aside className="node-library">
          <h3>Библиотека нод</h3>
          <p className="muted">Перетащите блок на поле</p>
          {[...new Set(catalog.map((c) => c.category))].map((cat) => (
            <section key={cat}>
              <h4>{cat}</h4>
              {catalog
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
