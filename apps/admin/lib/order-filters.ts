'use client';

/**
 * One filter model for the dashboard and the orders list, held in the URL.
 *
 * The dashboard's scorecards and the orders table filter the same ledger by the
 * same things — a date range, a set of statuses, a set of carriers, a search —
 * so they share one definition of what a filter *is* and one place it lives: the
 * query string. Keeping it in the URL is what makes a filtered view survive a
 * refresh, be copy-pasted to a colleague, and carry from a scorecard click on
 * the dashboard straight into the orders list without re-picking anything.
 *
 * Param names are short and stable (`from`, `to`, `status`, `courier`, `q`,
 * `branch`); the multi-selects repeat their key (`?status=delivered&status=…`).
 */

import { useCallback, useMemo } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

export interface OrderFilters {
  /** ISO dates; both empty ⇒ the live current day (dashboard) / all time (list). */
  from: string;
  to: string;
  statuses: string[];
  couriers: string[];
  search: string;
  /** Orders-list only; the dashboard ignores it. */
  branch: string;
}

export const EMPTY_FILTERS: OrderFilters = {
  from: '',
  to: '',
  statuses: [],
  couriers: [],
  search: '',
  branch: '',
};

export function parseFilters(params: URLSearchParams): OrderFilters {
  return {
    from: params.get('from') ?? '',
    to: params.get('to') ?? '',
    statuses: params.getAll('status'),
    couriers: params.getAll('courier'),
    search: params.get('q') ?? '',
    branch: params.get('branch') ?? '',
  };
}

/** Serialise filters to a query string (no leading `?`), omitting empties. */
export function filtersToQuery(f: OrderFilters): string {
  const p = new URLSearchParams();
  if (f.from) p.set('from', f.from);
  if (f.to) p.set('to', f.to);
  for (const s of f.statuses) p.append('status', s);
  for (const c of f.couriers) p.append('courier', c);
  if (f.search) p.set('q', f.search);
  if (f.branch) p.set('branch', f.branch);
  return p.toString();
}

/** Whether any filter is set — for showing a "clear all" affordance. */
export function hasAnyFilter(f: OrderFilters): boolean {
  return Boolean(
    f.from || f.to || f.statuses.length || f.couriers.length || f.search || f.branch,
  );
}

/** The `dashboardApi.today` params for these filters. */
export function toDashboardParams(f: OrderFilters) {
  const ready = Boolean(f.from) === Boolean(f.to);
  return {
    date_from: ready ? f.from || undefined : undefined,
    date_to: ready ? f.to || undefined : undefined,
    statuses: f.statuses.length ? f.statuses : undefined,
    couriers: f.couriers.length ? f.couriers : undefined,
  };
}

/** The `ordersApi.listAll` params for these filters (list adds search + branch). */
export function toOrdersParams(f: OrderFilters) {
  const ready = Boolean(f.from) === Boolean(f.to);
  return {
    date_from: ready ? f.from || undefined : undefined,
    date_to: ready ? f.to || undefined : undefined,
    statuses: f.statuses.length ? f.statuses : undefined,
    couriers: f.couriers.length ? f.couriers : undefined,
    search: f.search || undefined,
    branch_id: f.branch || undefined,
  };
}

/** An `/orders?…` link carrying the given filters, with optional overrides. */
export function ordersHref(f: OrderFilters, overrides?: Partial<OrderFilters>): string {
  const q = filtersToQuery({ ...f, ...overrides });
  return q ? `/orders?${q}` : '/orders';
}

/**
 * Read and write the shared filters through the URL of the current page.
 *
 * `patch` merges a change and replaces the URL (no new history entry, no scroll
 * jump). `toggleStatus`/`toggleCourier` flip one value in their set. Writes are
 * to whatever page called the hook — the dashboard stays on `/`, the list on
 * `/orders` — so a filter change is a same-page navigation, not a jump.
 */
export function useOrderFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const filters = useMemo(
    () => parseFilters(new URLSearchParams(params.toString())),
    [params],
  );

  const commit = useCallback(
    (next: OrderFilters) => {
      const q = filtersToQuery(next);
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  const patch = useCallback(
    (partial: Partial<OrderFilters>) => commit({ ...filters, ...partial }),
    [commit, filters],
  );

  const toggleIn = useCallback(
    (key: 'statuses' | 'couriers', value: string) => {
      const set = filters[key];
      patch({
        [key]: set.includes(value)
          ? set.filter(v => v !== value)
          : [...set, value],
      });
    },
    [filters, patch],
  );

  const toggleStatus = useCallback((v: string) => toggleIn('statuses', v), [toggleIn]);
  const toggleCourier = useCallback((v: string) => toggleIn('couriers', v), [toggleIn]);
  const clearAll = useCallback(() => commit(EMPTY_FILTERS), [commit]);

  return { filters, patch, toggleStatus, toggleCourier, clearAll };
}
