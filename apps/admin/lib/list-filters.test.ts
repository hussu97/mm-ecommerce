import { describe, expect, it } from 'vitest';

import {
  emptyFilterState,
  hasAnyFilterSet,
  parseUrlFilters,
  toggleValue,
  urlFiltersToQuery,
  type FilterFieldSpec,
} from './list-filters';

const FIELDS: FilterFieldSpec[] = [
  { key: 'search', param: 'q', kind: 'single' },
  { key: 'status', kind: 'single' },
  { key: 'kinds', param: 'kind', kind: 'multi' },
];

describe('list-filters', () => {
  it('empty state is per-kind', () => {
    expect(emptyFilterState(FIELDS)).toEqual({ search: '', status: '', kinds: [] });
  });

  it('parses single and multi params, honouring param aliases', () => {
    const p = new URLSearchParams('q=cho&status=active&kind=a&kind=b');
    expect(parseUrlFilters(FIELDS, p)).toEqual({ search: 'cho', status: 'active', kinds: ['a', 'b'] });
  });

  it('serialises omitting empties and repeating multi keys', () => {
    expect(urlFiltersToQuery(FIELDS, { search: 'cho', status: '', kinds: ['a', 'b'] })).toBe(
      'q=cho&kind=a&kind=b',
    );
    expect(urlFiltersToQuery(FIELDS, emptyFilterState(FIELDS))).toBe('');
  });

  it('round-trips parse → serialise → parse', () => {
    const start = 'q=choc&status=active&kind=a&kind=b';
    const state = parseUrlFilters(FIELDS, new URLSearchParams(start));
    const round = parseUrlFilters(FIELDS, new URLSearchParams(urlFiltersToQuery(FIELDS, state)));
    expect(round).toEqual(state);
  });

  it('hasAnyFilterSet reflects any set field', () => {
    expect(hasAnyFilterSet(FIELDS, emptyFilterState(FIELDS))).toBe(false);
    expect(hasAnyFilterSet(FIELDS, { search: '', status: '', kinds: ['a'] })).toBe(true);
    expect(hasAnyFilterSet(FIELDS, { search: 'x', status: '', kinds: [] })).toBe(true);
  });

  it('toggleValue adds then removes', () => {
    expect(toggleValue([], 'a')).toEqual(['a']);
    expect(toggleValue(['a'], 'b')).toEqual(['a', 'b']);
    expect(toggleValue(['a', 'b'], 'a')).toEqual(['b']);
    expect(toggleValue(undefined, 'a')).toEqual(['a']);
  });
});
