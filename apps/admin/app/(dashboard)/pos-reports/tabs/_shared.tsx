'use client';

/**
 * What every report tab needs: the fetch hook, the cache key, and the three
 * presentational pieces they all render into.
 */

import { useEffect, useRef, useState } from 'react';
import { ApiError } from '@/lib/api';
import { Spinner } from '@/components/ui';
import type { SalesBreakdownRow } from '@/lib/pos-types';
import { formatCurrency } from '@/lib/utils';

import type { Window } from '../report-window';

/**
 * Runs a report fetch and re-runs it whenever `key` changes.
 *
 * The fetcher is held in a ref rather than in the dependency list: it is a fresh
 * closure on every render, so depending on it directly would refetch forever.
 * `key` is the honest dependency — it encodes the filters the report is for.
 */
export function useReport<T>(fetcher: () => Promise<T>, key: string) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const fetcherRef = useRef(fetcher);

  // Declared before the fetch effect so it has already refreshed by the time
  // the fetch runs. Mutating a ref during render is not safe under concurrent
  // rendering, so it happens in an effect instead.
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetcherRef
      .current()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError('');
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load report.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return { data, loading, error };
}

/** Stable cache key for a report window. */
export function windowKey(w: Window, ...extra: string[]): string {
  return [w.branch_id ?? '', w.date_from ?? '', w.date_to ?? '', ...extra].join('|');
}

export function Panel({
  loading,
  error,
  empty,
  children,
}: {
  loading: boolean;
  error: string;
  empty?: boolean;
  children: React.ReactNode;
}) {
  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
        {error}
      </div>
    );
  }
  if (empty) {
    return (
      <p className="py-16 text-center text-sm text-gray-400 font-body">
        No data for this period.
      </p>
    );
  }
  return <>{children}</>;
}

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded border border-gray-200 bg-white p-4">
      <p className="text-[11px] uppercase tracking-widest text-gray-400 font-body">{label}</p>
      <p className="font-display text-xl text-primary mt-1">{value}</p>
      {hint && <p className="text-[11px] text-gray-400 font-body mt-0.5">{hint}</p>}
    </div>
  );
}

export function BreakdownTable({ rows, unitLabel }: { rows: SalesBreakdownRow[]; unitLabel: string }) {
  return (
    <div className="overflow-x-auto rounded border border-gray-200 bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            <th className="px-3 py-2 text-left">{unitLabel}</th>
            <th className="px-3 py-2 text-right">{rows[0]?.quantity !== undefined ? 'Qty' : 'Orders'}</th>
            <th className="px-3 py-2 text-right">Discounts</th>
            <th className="px-3 py-2 text-right">Net sales</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-gray-100 last:border-0">
              <td className="px-3 py-2 font-medium">{r.label}</td>
              <td className="px-3 py-2 text-right">{r.quantity ?? r.orders ?? 0}</td>
              <td className="px-3 py-2 text-right text-gray-500">{formatCurrency(r.discounts)}</td>
              <td className="px-3 py-2 text-right">{formatCurrency(r.net_sales)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A plain report table, for the reports whose shape is just rows. */
export function ReportTable({
  head,
  rows,
}: {
  head: string[];
  rows: (string | number)[][];
}) {
  return (
    <div className="overflow-x-auto rounded border border-gray-200 bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            {head.map((h, i) => (
              <th key={h} className={`px-3 py-2 ${i === 0 ? 'text-left' : 'text-right'}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className="border-b border-gray-100 last:border-0">
              {r.map((c, ci) => (
                <td
                  key={ci}
                  className={`px-3 py-2 ${ci === 0 ? 'font-medium' : 'text-right'}`}
                >
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
