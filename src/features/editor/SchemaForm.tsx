import type { ParamSchema } from '../../shared/api/nodeTypes';

type Props = {
  schema: ParamSchema;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
};

export function SchemaForm({schema, values, onChange}: Props) {
  const properties = Object.entries(schema.properties ?? {});
  if (!properties.length) return <p className="muted">У этой ноды нет настраиваемых параметров.</p>;
  return <>
    {properties.map(([key, field]) => {
      const id = `node-param-${key}`;
      const value = values[key] ?? (field.default == null ? '' : String(field.default));
      const required = schema.required?.includes(key) ?? false;
      const label = field.title ?? key;
      return <label key={key} htmlFor={id}>
        {label}{required ? ' *' : ''}
        {field.type === 'boolean' ? (
          <input id={id} type="checkbox" checked={value === 'true'} onChange={e=>onChange(key, String(e.target.checked))}/>
        ) : field.enum?.length ? (
          <select id={id} value={value} onChange={e=>onChange(key,e.target.value)} required={required}>
            <option value="">Выберите значение</option>
            {field.enum.map(option => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
          </select>
        ) : (
          <input id={id} type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
            step={field.type === 'integer' ? 1 : 'any'} min={field.minimum} max={field.maximum}
            placeholder={field.placeholder} value={value} required={required}
            onChange={e=>onChange(key,e.target.value)} />
        )}
        {field.description && <small className="muted">{field.description}</small>}
      </label>;
    })}
  </>;
}
