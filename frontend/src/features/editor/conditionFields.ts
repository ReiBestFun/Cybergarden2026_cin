import type { ParamSchema } from '../../shared/api/nodeTypes';
import type { JsonValue } from '../../shared/api/workflows';
import type { ConditionRow } from './types';

const counter = { type: 'integer', operators: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte'], initial: '0' } as const;
const coverage = { type: 'string', operators: ['eq', 'ne', 'matches'], initial: 'complete' } as const;
const verdict = { type: 'string', operators: ['eq', 'ne'], initial: 'passed', choices: ['passed', 'blocked', 'error'] } as const;

function operand(field: string | undefined) {
  if (field?.startsWith('summary.')) return counter;
  if (field === 'coverage.status') return coverage;
  if (field === 'ci.decision') return verdict;
}

export function changeConditionField(row: ConditionRow, field: string): ConditionRow {
  const next = operand(field);
  if (!next || next === operand(row.field)) return { ...row, field };
  const operators: readonly string[] = next.operators;
  return { field, operator: operators.includes(row.operator) ? row.operator : 'eq', value: next.initial };
}

export function comparisonSchema(schema: ParamSchema, field: string | undefined): ParamSchema {
  const spec = operand(field);
  if (!spec) return schema;
  return { ...schema, properties: { ...schema.properties,
    operator: { ...schema.properties?.operator, enum: [...spec.operators] },
    value: { ...schema.properties?.value, type: spec.type, enum: 'choices' in spec ? [...spec.choices] : undefined },
  } };
}

export function encodeCondition(row: ConditionRow): Record<string, JsonValue> {
  const result: Record<string, JsonValue> = {};
  if (row.field) result.field = row.field;
  if (row.operator) result.operator = row.operator;
  if (row.value !== undefined && row.value !== '') {
    result.value = operand(row.field)?.type === 'integer' && /^-?[0-9]+$/.test(row.value.trim())
      ? Number(row.value) : row.value;
  }
  return result;
}
