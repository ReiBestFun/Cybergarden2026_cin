import type { ParamSchema } from '../../shared/api/nodeTypes';
import { SchemaForm } from './SchemaForm';
import { changeConditionField, comparisonSchema } from './conditionFields';
import type { ConditionGroup, ConditionRow, FlowData } from './types';

const defaultRow = { field: 'summary.total', operator: 'gt', value: '0' };

type Props = {
  data: FlowData;
  schema: ParamSchema;
  onChange: (mode: ConditionGroup['mode'], rows: ConditionRow[]) => void;
};

export function ConditionSettings({ data, schema, onChange }: Props) {
  const mode = data.conditionGroup?.mode ?? 'AND';
  const rows = data.conditionGroup?.conditions ?? [data.config ?? {}];

  function edit(index: number, key: string, value: string) {
    const updated = key === 'field' ? changeConditionField(rows[index], value) : { ...rows[index], [key]: value };
    onChange(mode, rows.map((row, i) => i === index ? updated : row));
  }

  return <>
    {rows.length > 1 && <label>Объединение условий
      <select value={mode} onChange={event => onChange(event.target.value === 'OR' ? 'OR' : 'AND', rows)}>
        <option value="AND">AND · Все условия</option>
        <option value="OR">OR · Хотя бы одно условие</option>
      </select>
    </label>}
    {rows.map((row, index) => <fieldset key={index} className="condition-row">
      <legend>Условие {index + 1}</legend>
      <SchemaForm schema={comparisonSchema(schema, row.field)} values={row} idPrefix={`condition-${index}`}
        onChange={(key, value) => edit(index, key, value)} />
      {rows.length > 1 && <button type="button" className="btn secondary"
        aria-label={`Удалить условие ${index + 1}`} onClick={() => onChange(mode, rows.filter((_, i) => i !== index))}>Удалить</button>}
    </fieldset>)}
    <button type="button" className="btn secondary" onClick={() => onChange(mode, [...rows, defaultRow])}>Добавить условие</button>
  </>;
}
