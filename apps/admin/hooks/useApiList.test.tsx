import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useApiList, type PaginatedResult } from './useApiList';

/** A promise the test resolves by hand, to order responses deliberately. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const page = (items: string[], total = items.length): PaginatedResult<string> => ({
  items,
  total,
  pages: 1,
});

describe('useApiList (server mode)', () => {
  it('loads on mount and exposes the rows', async () => {
    const fetch = vi.fn(async () => page(['a', 'b']));
    const { result } = renderHook(() => useApiList({ paginate: 'server', fetch }));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual(['a', 'b']);
    expect(result.current.total).toBe(2);
    expect(fetch).toHaveBeenCalledWith(1, 50);
  });

  it('surfaces a failed load through loadError', async () => {
    const fetch = vi.fn(async () => { throw new Error('HTTP 500'); });
    const { result } = renderHook(() => useApiList({ paginate: 'server', fetch }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.loadError).toBe('HTTP 500');
    expect(result.current.items).toEqual([]);
  });

  it('drops a stale response that resolves after a newer one', async () => {
    // Request A (initial) resolves LAST; request B (after refetch) first.
    const a = deferred<PaginatedResult<string>>();
    const b = deferred<PaginatedResult<string>>();
    const responses = [a.promise, b.promise];
    const fetch = vi.fn(() => responses.shift()!);

    const { result } = renderHook(() => useApiList({ paginate: 'server', fetch }));
    await act(async () => { void result.current.refetch(); });

    b.resolve(page(['fresh']));
    await waitFor(() => expect(result.current.items).toEqual(['fresh']));

    // The stale first response lands late and must not overwrite the fresh one.
    a.resolve(page(['stale']));
    await act(async () => { await a.promise; });
    expect(result.current.items).toEqual(['fresh']);
  });

  it('resets to page 1 and refetches when the fetcher identity changes', async () => {
    const fetch1 = vi.fn(async (p: number) => page([`one-${p}`], 200));
    const fetch2 = vi.fn(async (p: number) => page([`two-${p}`], 200));

    const { result, rerender } = renderHook(
      ({ fetch }) => useApiList({ paginate: 'server', fetch }),
      { initialProps: { fetch: fetch1 } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.setPage(3));
    await waitFor(() => expect(result.current.items).toEqual(['one-3']));

    // A filter change (new fetcher) lands back on page 1 — one request, not a
    // page-3 fetch racing a page-1 fetch.
    rerender({ fetch: fetch2 });
    await waitFor(() => expect(result.current.items).toEqual(['two-1']));
    expect(result.current.page).toBe(1);
    expect(fetch2).toHaveBeenCalledTimes(1);
    expect(fetch2).toHaveBeenCalledWith(1, 50);
  });
});

describe('useApiList (client mode)', () => {
  it('returns the full result and leaves slicing to the page', async () => {
    const fetch = vi.fn(async () => ['x', 'y', 'z']);
    const { result } = renderHook(() => useApiList({ paginate: 'client', fetch }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual(['x', 'y', 'z']);
    expect(result.current.total).toBe(3);
    expect(fetch).toHaveBeenCalledTimes(1);

    // Paging is local state only — no new request.
    act(() => result.current.setPage(2));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(result.current.page).toBe(2);
  });

  it('refetch reloads with the same fetcher', async () => {
    let call = 0;
    const fetch = vi.fn(async () => [`load-${++call}`]);
    const { result } = renderHook(() => useApiList({ paginate: 'client', fetch }));

    await waitFor(() => expect(result.current.items).toEqual(['load-1']));
    await act(async () => { await result.current.refetch(); });
    expect(result.current.items).toEqual(['load-2']);
  });
});
