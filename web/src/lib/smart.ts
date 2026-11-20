// Smart playlist rule helpers (v7): defaults for new rows, readable summaries, tree edits.
// The server validates and evaluates; these only keep the editor's state well-formed.
import type { SmartDefinition, SmartFieldSpec, SmartGroup, SmartRule } from './types';

export const OP_LABELS: Record<string, string> = {
  eq: 'is',
  ne: 'is not',
  gt: 'is more than',
  gte: 'is at least',
  lt: 'is less than',
  lte: 'is at most',
  between: 'is between',
  is: 'is',
  isNot: 'is not',
  contains: 'contains',
  notContains: "doesn't contain",
  startsWith: 'starts with',
  inLast: 'in the last',
  notInLast: 'not in the last',
  before: 'before',
  after: 'after',
  never: 'never',
  ever: 'ever',
};

export const isGroup = (r: SmartRule | SmartGroup): r is SmartGroup => 'rules' in r;

export function emptyDefinition(): SmartDefinition {
  return { match: 'all', rules: [], sort: 'random', limit: { type: 'none' } };
}

/** A sensible value for a field + operator (used when either changes). */
export function defaultValue(f: SmartFieldSpec, op: string): SmartRule['value'] {
  switch (f.type) {
    case 'number':
      if (op === 'between') return f.id === 'year' ? [1990, 1999] : f.id === 'tempo' ? [100, 130] : [1, 10];
      return f.id === 'year' ? 2000 : f.id === 'rating' ? 4 : f.id === 'tempo' ? 120 : f.id === 'durationS' ? 300 : 1;
    case 'percent':
      return op === 'between' ? [0.3, 0.7] : 0.5;
    case 'text':
      return '';
    case 'date':
      if (op === 'inLast' || op === 'notInLast') return 1;
      if (op === 'before' || op === 'after') return new Date().toISOString().slice(0, 10);
      return null;
    case 'bool':
      return true;
    case 'enum':
      return f.options?.[0] ?? '';
    case 'playlist':
      return '';
  }
}

export function newRule(f: SmartFieldSpec, op = f.ops[0]): SmartRule {
  const r: SmartRule = { field: f.id, op, value: defaultValue(f, op) };
  if (f.type === 'date' && (op === 'inLast' || op === 'notInLast')) r.unit = 'months';
  return r;
}

/** Change a rule's field or operator, keeping the value when it still fits. */
export function retarget(r: SmartRule, f: SmartFieldSpec, op?: string): SmartRule {
  const nextOp = op && f.ops.includes(op) ? op : f.ops.includes(r.op) && r.field === f.id ? r.op : f.ops[0];
  const sameKind = r.field === f.id && (nextOp === 'between') === (r.op === 'between');
  const out: SmartRule = { field: f.id, op: nextOp, value: sameKind ? r.value : defaultValue(f, nextOp) };
  if (f.type === 'date') {
    if (nextOp === 'inLast' || nextOp === 'notInLast') {
      out.unit = r.unit ?? 'months';
      if (typeof out.value !== 'number') out.value = 1;
    } else if (nextOp === 'before' || nextOp === 'after') {
      if (typeof out.value !== 'string') out.value = defaultValue(f, nextOp);
    } else out.value = null;
  }
  return out;
}

const pct = (v: unknown) => `${Math.round(Number(v) * 100)}%`;

/** "Play count is at least 5", "Last played not in the last 1 month". */
export function describeRule(r: SmartRule, fields: SmartFieldSpec[], playlistName?: (id: string) => string | undefined): string {
  const f = fields.find((x) => x.id === r.field);
  const label = f?.label ?? r.field;
  const op = OP_LABELS[r.op] ?? r.op;
  const fmt = (v: unknown) => (f?.type === 'percent' ? pct(v) : String(v));
  if (r.op === 'never' || r.op === 'ever') return `${label}: ${op}`;
  if (r.op === 'between' && Array.isArray(r.value)) return `${label} ${op} ${fmt(r.value[0])} and ${fmt(r.value[1])}`;
  if (r.op === 'inLast' || r.op === 'notInLast') {
    const n = Number(r.value);
    const unit = (r.unit ?? 'days').replace(/s$/, '');
    return `${label} ${op} ${n} ${unit}${n === 1 ? '' : 's'}`;
  }
  if (f?.type === 'bool') return `${label} ${r.op === 'is' ? 'is' : 'is not'} ${r.value ? 'yes' : 'no'}`;
  if (f?.type === 'playlist') return `${label} ${op} "${playlistName?.(String(r.value)) ?? r.value}"`;
  return `${label} ${op} ${f?.type === 'text' ? `"${r.value}"` : fmt(r.value)}`;
}

export function describeDefinition(d: SmartDefinition, fields: SmartFieldSpec[]): string {
  const parts = d.rules.map((r) => (isGroup(r) ? `(${r.rules.map((x) => (isGroup(x) ? '…' : describeRule(x, fields))).join(r.match === 'all' ? ' and ' : ' or ')})` : describeRule(r, fields)));
  return parts.length ? parts.join(d.match === 'all' ? ' and ' : ' or ') : 'All songs';
}

/** Rules that are not ready to send (empty text, no playlist chosen). */
export function incomplete(g: SmartGroup, fields: SmartFieldSpec[]): boolean {
  return g.rules.some((r) => {
    if (isGroup(r)) return incomplete(r, fields);
    const f = fields.find((x) => x.id === r.field);
    if (!f) return true;
    if ((f.type === 'text' || f.type === 'playlist') && !String(r.value ?? '').trim()) return true;
    if ((f.type === 'number' || f.type === 'percent') && (r.value === null || r.value === undefined || (Array.isArray(r.value) ? r.value.some((x) => !Number.isFinite(x)) : !Number.isFinite(Number(r.value))))) return true;
    return false;
  });
}

export function fmtDuration(s: number): string {
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}
