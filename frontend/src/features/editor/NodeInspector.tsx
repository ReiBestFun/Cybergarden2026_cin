import { useState } from 'react';
import type { NodeRun } from '../../shared/api/workflows';

const tabs = [
  { key: 'input_preview', label: 'Вход' },
  { key: 'output_preview', label: 'Выход' },
  { key: 'error', label: 'Ошибка' },
] as const;

const messages = {
  input_preview: 'Входных данных пока нет.',
  output_preview: 'Выходных данных пока нет.',
  error: 'Ошибок нет.',
};

export function NodeInspector({ node }: { node: NodeRun }) {
  const [active, setActive] = useState<(typeof tabs)[number]['key']>(node.error ? 'error' : 'output_preview');
  const data = node[active];
  const noInputs = active === 'input_preview' && Array.isArray(node.input_preview?.inputs) && node.input_preview.inputs.length === 0;
  return (
    <section className="node-run" aria-label="Данные выполнения ноды">
      <div className="segments inspector-tabs" role="group" aria-label="Раздел данных">
        {tabs.map(({ key, label }) => <button className="segment" type="button" key={key} aria-pressed={active === key}
          onClick={() => setActive(key)}>{label}{key === 'error' && node.error ? ' •' : ''}</button>)}
      </div>
      {active === 'error' && node.error ? (
        <div className="notice error"><span>{node.error.message} <code>{node.error.code}</code>
          {node.error.hint && <p>{node.error.hint}</p>}</span></div>
      ) : data && !noInputs ? <pre tabIndex={0}>{JSON.stringify(data, null, 2)}</pre>
        : <p className="muted">{noInputs ? 'У запуска этой ноды нет входящих связей.' : messages[active]}</p>}
    </section>
  );
}
