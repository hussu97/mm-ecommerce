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

/* ------------------------------------------------------------------ *
 * Quick date-range presets (Today, Yesterday, L7D, …).
 *
 * Every date the shop cares about is a calendar date in Asia/Dubai, not in
 * whoever's browser is open (see `SHOP_TZ` in lib/utils.ts). So "today" must be
 * computed against the shop's clock — a laptop on London time would otherwise
 * put a preset a day off after midnight Dubai. We take the shop's current
 * Y-M-D, anchor it at UTC midnight, and do the day/month arithmetic there so
 * it is immune to DST (Dubai has none, but the anchor keeps it honest either
 * way), then serialise back to the `YYYY-MM-DD` the API expects.
 * ------------------------------------------------------------------ */

const SHOP_TZ = 'Asia/Dubai';

/** A `Date` anchored at UTC midnight of the shop's *current* calendar day. */
function shopTodayAnchor(): Date {
  // en-CA renders as YYYY-MM-DD, which we can split without locale surprises.
  const ymd = new Intl.DateTimeFormat('en-CA', {
    timeZone: SHOP_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
  const [y, m, d] = ymd.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

/** `YYYY-MM-DD` for a UTC-anchored date. */
function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(d: Date, days: number): Date {
  const next = new Date(d);
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

export interface DatePreset {
  key: string;
  label: string;
  /** Compute the inclusive `{ from, to }` for this preset, in shop time. */
  range: () => { from: string; to: string };
}

/**
 * The quick ranges offered on the dashboard and orders list. Order matters —
 * this is the left-to-right order the chips render in.
 */
export const DATE_PRESETS: DatePreset[] = [
  {
    key: 'today',
    label: 'Today',
    range: () => {
      const t = isoDay(shopTodayAnchor());
      return { from: t, to: t };
    },
  },
  {
    key: 'yesterday',
    label: 'Yesterday',
    range: () => {
      const y = isoDay(addDays(shopTodayAnchor(), -1));
      return { from: y, to: y };
    },
  },
  {
    key: 'l7d',
    label: 'L7D',
    range: () => {
      const today = shopTodayAnchor();
      return { from: isoDay(addDays(today, -6)), to: isoDay(today) };
    },
  },
  {
    key: 'this_month',
    label: 'This month',
    range: () => {
      const today = shopTodayAnchor();
      const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1));
      return { from: isoDay(first), to: isoDay(today) };
    },
  },
  {
    key: 'last_month',
    label: 'Last month',
    range: () => {
      const today = shopTodayAnchor();
      const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1));
      // Day 0 of this month is the last day of the previous month.
      const last = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 0));
      return { from: isoDay(first), to: isoDay(last) };
    },
  },
  {
    key: 'l30d',
    label: 'L30D',
    range: () => {
      const today = shopTodayAnchor();
      return { from: isoDay(addDays(today, -29)), to: isoDay(today) };
    },
  },
];

/** The preset key whose range matches the current `from`/`to`, if any. */
export function activePresetKey(f: OrderFilters): string | null {
  if (!f.from || !f.to) return null;
  for (const p of DATE_PRESETS) {
    const r = p.range();
    if (r.from === f.from && r.to === f.to) return p.key;
  }
  return null;
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
