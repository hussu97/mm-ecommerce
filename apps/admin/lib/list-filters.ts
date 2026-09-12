'use client';

/**
 * A URL-backed filter model for any list, generalised from `lib/order-filters`.
 *
 * The orders list proved the shape worth copying: filters live in the query
 * string, so a filtered view survives a refresh, pastes to a colleague, and
 * carries between pages. That file is the hand-tuned instance for orders (it also
 * owns date presets and the API param mapping); this is the same mechanics for
 * an arbitrary set of fields, so a migrated list gets URL-persisted filters
 * without re-deriving `parse`/`serialise`/`toggle`/`clear` each time.
 *
 * Declare the fields once; a `single` field is one param (`?status=active`), a
 * `multi` field repeats its key (`?kind=a&kind=b`). A date is just a `single`
 * string field.
 */

import { useCallback, useMemo } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

export type FilterKind = 'single' | 'multi';

export interface FilterFieldSpec {
  /** Key in the returned state object. */
  key: string;
  /** URL parameter name. Defaults to `key`. */
  param?: string;
  kind: FilterKind;
}

/** A `single` field holds a string; a `multi` field holds a string[]. */
export type FilterState = Record<string, string | string[]>;

const paramOf = (f: FilterFieldSpec) => f.param ?? f.key;

/** The zero value: `''` for single fields, `[]` for multi. */
export function emptyFilterState(fields: FilterFieldSpec[]): FilterState {
  const out: FilterState = {};
  for (const f of fields) out[f.key] = f.kind === 'multi' ? [] : '';
  return out;
}

export function parseUrlFilters(fields: FilterFieldSpec[], params: URLSearchParams): FilterState {
  const out: FilterState = {};
  for (const f of fields) {
    out[f.key] = f.kind === 'multi' ? params.getAll(paramOf(f)) : (params.get(paramOf(f)) ?? '');
  }
  return out;
}

/** Serialise to a query string (no leading `?`), omitting empties. */
export function urlFiltersToQuery(fields: FilterFieldSpec[], state: FilterState): string {
  const p = new URLSearchParams();
  for (const f of fields) {
    const v = state[f.key];
    if (f.kind === 'multi') {
      for (const item of (v as string[]) ?? []) if (item) p.append(paramOf(f), item);
    } else if (v) {
      p.set(paramOf(f), v as string);
    }
  }
  return p.toString();
}

/** Whether any field is set — for a "Clear all" affordance. */
export function hasAnyFilterSet(fields: FilterFieldSpec[], state: FilterState): boolean {
  return fields.some((f) => {
    const v = state[f.key];
    return f.kind === 'multi' ? ((v as string[]) ?? []).length > 0 : Boolean(v);
  });
}

/** Flip one value in a `multi` field's set; returns the next array. */
export function toggleValue(current: string[] | undefined, value: string): string[] {
  const set = current ?? [];
  return set.includes(value) ? set.filter((v) => v !== value) : [...set, value];
}

export interface UseUrlFiltersResult<S extends FilterState> {
  filters: S;
  patch: (partial: Partial<S>) => void;
  toggle: (key: keyof S & string, value: string) => void;
  clearAll: () => void;
  hasAny: boolean;
}

/**
 * Read and write list filters through the current page's URL.
 *
 * `patch` merges and replaces the URL (no history entry, no scroll jump);
 * `toggle` flips one value in a `multi` field; `clearAll` resets to empty. Writes
 * stay on the current page, so a filter change is a same-page navigation.
 */
export function useUrlFilters<S extends FilterState = FilterState>(
  fields: FilterFieldSpec[],
): UseUrlFiltersResult<S> {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const filters = useMemo(
    () => parseUrlFilters(fields, new URLSearchParams(params.toString())) as S,
    // `fields` is a stable module-level constant at every call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [params],
  );

  const commit = useCallback(
    (next: S) => {
      const q = urlFiltersToQuery(fields, next);
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [router, pathname],
  );

  const patch = useCallback(
    (partial: Partial<S>) => commit({ ...filters, ...partial }),
    [commit, filters],
  );

  const toggle = useCallback(
    (key: keyof S & string, value: string) => {
      const next = toggleValue(filters[key] as string[], value);
      commit({ ...filters, [key]: next });
    },
    [commit, filters],
  );

  const clearAll = useCallback(
    () => commit(emptyFilterState(fields) as S),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [commit],
  );

  return { filters, patch, toggle, clearAll, hasAny: hasAnyFilterSet(fields, filters) };
}
