import { useCallback, useEffect, useRef, useState } from 'react';

/** The shape every paginated admin endpoint answers with. */
export interface PaginatedResult<T> {
  items: T[];
  total: number;
  pages: number;
}

interface ServerListOptions<T> {
  /**
   * `'server'`: the API paginates. `fetch` receives the current page and page
   * size, and `items` holds exactly one page of rows. Page and page-size
   * changes go back to the API.
   */
  paginate: 'server';
  /**
   * Must be referentially stable (wrap in `useCallback`) with the page's
   * filter values as dependencies — a new function identity is the signal
   * that the filters changed, which resets to page 1 and refetches.
   */
  fetch: (page: number, perPage: number) => Promise<PaginatedResult<T>>;
}

interface ClientListOptions<T> {
  /**
   * `'client'`: the API returns everything in one call and the page slices it
   * locally. `items` holds the full result; the hook still owns `page` /
   * `perPage` so the page can slice with them — usually after applying its
   * own tab/search filters, which is why the hook does not slice for you
   * (`pages` computed from the unfiltered length would lie). Page changes
   * are pure local state: no request is made.
   */
  paginate: 'client';
  /** Must be referentially stable (wrap in `useCallback`); see above. */
  fetch: () => Promise<T[]>;
}

export type UseApiListOptions<T> = ServerListOptions<T> | ClientListOptions<T>;

export interface UseApiListResult<T> {
  /** Server mode: the current page of rows. Client mode: the entire result. */
  items: T[];
  /** Server mode: totals from the API. Client mode: derived from `items` before any local filtering. */
  total: number;
  pages: number;
  page: number;
  perPage: number;
  setPage: (page: number) => void;
  setPerPage: (perPage: number) => void;
  loading: boolean;
  /** Non-empty when the last load failed — render it through `<LoadError>`. */
  loadError: string;
  /** Reload with the current filters/page. The standard follow-up to a mutation. */
  refetch: () => Promise<void>;
}

/**
 * One list, one loader, one owner for its state.
 *
 * Owns loading / error / rows / pagination for a list page, in either
 * pagination mode (see the `paginate` flag on the options). Two behaviours
 * every hand-rolled copy of this used to forget:
 *
 * - **Stale responses can't win.** Each load takes a ticket from a monotonic
 *   counter and only the latest ticket may write state, so typing "cho" then
 *   "chocolate" can never paint the "cho" results over the "chocolate" ones
 *   when the earlier request resolves later.
 * - **Failures are loud.** `loadError` feeds `<LoadError>` (see the rule at
 *   `components/ui/index.tsx`) instead of a `.catch` swallowing a 500 into an
 *   empty table.
 */
export function useApiList<T>(options: UseApiListOptions<T>): UseApiListResult<T> {
  const [items, setItems] = useState<T[]>([]);
  const [serverTotal, setServerTotal] = useState(0);
  const [serverPages, setServerPages] = useState(1);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  // Destructured so the effects depend on the pieces, not the options object,
  // whose literal identity changes every render.
  const { paginate, fetch } = options;

  /** Only the holder of the latest ticket may write state. */
  const seq = useRef(0);

  const refetch = useCallback(async () => {
    const ticket = ++seq.current;
    setLoading(true);
    setLoadError('');
    try {
      if (paginate === 'server') {
        const res = await (fetch as ServerListOptions<T>['fetch'])(page, perPage);
        if (ticket !== seq.current) return;
        setItems(res.items);
        setServerTotal(res.total);
        setServerPages(res.pages);
      } else {
        const rows = await (fetch as ClientListOptions<T>['fetch'])();
        if (ticket !== seq.current) return;
        setItems(rows);
      }
    } catch (err) {
      if (ticket !== seq.current) return;
      setLoadError((err as Error).message);
    } finally {
      if (ticket === seq.current) setLoading(false);
    }
  }, [paginate, fetch, page, perPage]);

  // When to actually load:
  // - A new fetcher identity means the filters changed: land on page 1 first
  //   and let the page-state change trigger the load, so a filter change from
  //   page 3 issues one request rather than a page-3 fetch immediately
  //   cancelled by a page-1 fetch. (`pendingReload` carries the intent across
  //   that reset for client mode, which otherwise ignores page changes.)
  // - Server mode reloads on page/perPage changes; client mode holds the full
  //   result already, so paging there is pure local state.
  const lastFetch = useRef(fetch);
  const firstLoad = useRef(true);
  const pendingReload = useRef(false);
  useEffect(() => {
    if (lastFetch.current !== fetch) {
      lastFetch.current = fetch;
      if (page !== 1) {
        pendingReload.current = true;
        setPage(1);
        return;
      }
      void refetch();
      return;
    }
    if (paginate === 'server' || firstLoad.current || pendingReload.current) {
      firstLoad.current = false;
      pendingReload.current = false;
      void refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch already carries fetch/page/perPage
  }, [fetch, page, perPage]);

  const total = paginate === 'server' ? serverTotal : items.length;
  const pages = paginate === 'server'
    ? serverPages
    : Math.max(1, Math.ceil(items.length / perPage));

  return { items, total, pages, page, perPage, setPage, setPerPage, loading, loadError, refetch };
}
