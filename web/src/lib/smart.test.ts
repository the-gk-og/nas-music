import { describe, expect, it } from 'vitest';
import { describeDefinition, describeRule, emptyDefinition, incomplete, newRule, retarget } from './smart';
import type { SmartFieldSpec } from './types';

const F: SmartFieldSpec[] = [
  { id: 'playCount', label: 'Play count', type: 'number', ops: ['eq', 'gte', 'between'] },
  { id: 'lastPlayed', label: 'Last played', type: 'date', ops: ['inLast', 'notInLast', 'before', 'never'] },
  { id: 'energy', label: 'Energy', type: 'percent', ops: ['gte', 'between'] },
  { id: 'genre', label: 'Genre', type: 'text', ops: ['is', 'contains'] },
  { id: 'favourite', label: 'Favourite', type: 'bool', ops: ['is', 'isNot'] },
  { id: 'mood', label: 'Mood', type: 'enum', ops: ['is', 'isNot'], options: ['happy', 'sad'] },
];

describe('smart rules', () => {
  it('builds defaults per type', () => {
    expect(newRule(F[0])).toEqual({ field: 'playCount', op: 'eq', value: 1 });
    expect(newRule(F[1])).toEqual({ field: 'lastPlayed', op: 'inLast', value: 1, unit: 'months' });
    expect(newRule(F[5]).value).toBe('happy');
  });
  it('retargets field and operator', () => {
    const r = { field: 'playCount', op: 'gte', value: 5 };
    expect(retarget(r, F[0], 'eq').value).toBe(5);
    expect(retarget(r, F[0], 'between').value).toEqual([1, 10]);
    expect(retarget(r, F[3])).toEqual({ field: 'genre', op: 'is', value: '' });
    expect(retarget({ field: 'lastPlayed', op: 'inLast', value: 2, unit: 'weeks' }, F[1], 'never').value).toBeNull();
    expect(retarget({ field: 'lastPlayed', op: 'never', value: null }, F[1], 'notInLast')).toMatchObject({ value: 1, unit: 'months' });
  });
  it('describes rules', () => {
    expect(describeRule({ field: 'playCount', op: 'gte', value: 5 }, F)).toBe('Play count is at least 5');
    expect(describeRule({ field: 'lastPlayed', op: 'notInLast', value: 1, unit: 'months' }, F)).toBe('Last played not in the last 1 month');
    expect(describeRule({ field: 'energy', op: 'between', value: [0.5, 0.8] }, F)).toBe('Energy is between 50% and 80%');
    expect(describeRule({ field: 'favourite', op: 'is', value: true }, F)).toBe('Favourite is yes');
    const d = { ...emptyDefinition(), rules: [{ field: 'genre', op: 'is', value: 'Rock' }, { match: 'any' as const, rules: [{ field: 'playCount', op: 'eq', value: 0 }, { field: 'favourite', op: 'is', value: true }] }] };
    expect(describeDefinition(d, F)).toBe('Genre is "Rock" and (Play count is 0 or Favourite is yes)');
    expect(describeDefinition(emptyDefinition(), F)).toBe('All songs');
  });
  it('spots incomplete rules', () => {
    expect(incomplete({ match: 'all', rules: [{ field: 'genre', op: 'is', value: ' ' }] }, F)).toBe(true);
    expect(incomplete({ match: 'all', rules: [{ match: 'any', rules: [{ field: 'playCount', op: 'eq', value: NaN }] }] }, F)).toBe(true);
    expect(incomplete({ match: 'all', rules: [{ field: 'playCount', op: 'between', value: [1, 3] }] }, F)).toBe(false);
  });
});
