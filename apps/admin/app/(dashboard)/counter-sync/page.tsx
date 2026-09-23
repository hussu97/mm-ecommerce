'use client';

/**
 * Counter sync — the local-first counter's review screen.
 *
 * A counter sale rung up on the iPad syncs afterwards. Most sync `verified`
 * and never appear here. This lists the ones that need a person:
 *
 * - **Needs review**: a pricing `mismatch` (the receipt's figures were booked;
 *   the server's are in the audit), an `unverified` re-price (the bundle the
 *   sale cited was unknown), a sale that landed on an already-closed till or
 *   day, or any other ingest flag.
 * - **Quarantined**: a paid sale that could not be booked at all (wrong
 *   branch, till or ticket prefix, unknown method…). The till cleared it from
 *   its outbox; resolve it here once it is dealt with.
 * - **Terminals**: each till's unsynced and parked sales, as its heartbeat
 *   last reported them.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import type { Schemas } from '@mm/types';
import { counterSyncApi, ApiError } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { Badge, Button, Input, LoadError, Spinner } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';
import { formatAge, formatCurrency, formatDateTime } from '@/lib/utils';

type Overview = Schemas['CounterSyncOverview'];
type Quarantined = Schemas['CounterQuarantineRow'];

const STATUS_VARIANT: Record<string, 'danger' | 'warning' | 'success' | 'neutral'> = {
  mismatch: 'danger',
  unverified: 'warning',
  verified: 'success',
};

export default function CounterSyncPage() {
  const toast = useToast();
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchId, setBranchId] = useState('');
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [notes, setNotes] = useState<Record<string, string>>({});

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await counterSyncApi.overview({ branch_id: branchId || undefined }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load counter sync');
    } finally {
      setLoading(false);
    }
  }, [branchId]);

  useEffect(() => {
    void load();
  }, [load]);

  const branchName = useMemo(() => {
    const byId = new Map(branches.map(b => [b.id, b.name]));
    return (id: string | null | undefined) => (id ? byId.get(id) ?? '—' : '—');
  }, [branches]);

  const resolve = async (row: Quarantined) => {
    const note = (notes[row.id] ?? '').trim();
    if (!note) {
      toast.error('Say how it was resolved');
      return;
    }
    try {
      await counterSyncApi.resolve(row.id, note);
      toast.success('Marked resolved');
      void load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not resolve');
    }
  };

  return (
    <div className="p-6 space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-xl text-primary tracking-wide">Counter Sync</h1>
          <p className="text-xs text-gray-500 font-body">
            Local-first counter sales that synced with something to look at, and each till&apos;s backlog.
          </p>
        </div>
        <label className="text-xs text-gray-500 font-body">
          Branch{' '}
          <select
            className="ml-1 border border-gray-200 px-2 py-1 text-sm"
            value={branchId}
            onChange={e => setBranchId(e.target.value)}
          >
            <option value="">All branches</option>
            {branches.map(b => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
        </label>
      </div>

      {loading && !data ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : error ? (
        <LoadError message={error} onRetry={() => void load()} />
      ) : data ? (
        <>
          <section>
            <h2 className="text-sm font-body font-medium uppercase tracking-wider text-gray-600 mb-2">
              Terminals
            </h2>
            <div className="overflow-x-auto border border-gray-200">
              <table className="w-full text-sm font-body">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                  <tr>
                    <th className="text-left px-3 py-2">Terminal</th>
                    <th className="text-left px-3 py-2">Branch</th>
                    <th className="text-left px-3 py-2">Prefix</th>
                    <th className="text-left px-3 py-2">Build</th>
                    <th className="text-left px-3 py-2">Mode</th>
                    <th className="text-right px-3 py-2">Unsynced</th>
                    <th className="text-right px-3 py-2">Parked</th>
                    <th className="text-left px-3 py-2">Oldest</th>
                  </tr>
                </thead>
                <tbody>
                  {data.devices.map(d => (
                    <tr key={d.device_id} className="border-t border-gray-100">
                      <td className="px-3 py-2 font-medium">{d.name}</td>
                      <td className="px-3 py-2">{branchName(d.branch_id)}</td>
                      <td className="px-3 py-2"><code className="text-xs">{d.ticket_prefix ?? '—'}</code></td>
                      <td className="px-3 py-2 text-xs text-gray-600">{d.build_number ?? '—'}</td>
                      <td className="px-3 py-2 text-xs">{d.counter_mode ?? 'online'}</td>
                      <td className="px-3 py-2 text-right">
                        {d.pending_sales ? <Badge variant="warning">{d.pending_sales}</Badge> : (d.pending_sales ?? '—')}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {d.parked_sales ? <Badge variant="danger">{d.parked_sales}</Badge> : (d.parked_sales ?? '—')}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">
                        {d.oldest_pending_sale_at ? `${formatAge(d.oldest_pending_sale_at)} ago` : '—'}
                      </td>
                    </tr>
                  ))}
                  {data.devices.length === 0 && (
                    <tr><td colSpan={8} className="px-3 py-6 text-center text-gray-400">No terminals.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2 className="text-sm font-body font-medium uppercase tracking-wider text-gray-600 mb-2">
              Needs review ({data.orders.length})
            </h2>
            <div className="overflow-x-auto border border-gray-200">
              <table className="w-full text-sm font-body">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                  <tr>
                    <th className="text-left px-3 py-2">Ticket</th>
                    <th className="text-left px-3 py-2">Branch</th>
                    <th className="text-left px-3 py-2">Day</th>
                    <th className="text-right px-3 py-2">Total</th>
                    <th className="text-left px-3 py-2">Pricing</th>
                    <th className="text-left px-3 py-2">Flags</th>
                    <th className="text-left px-3 py-2">Synced</th>
                  </tr>
                </thead>
                <tbody>
                  {data.orders.map(o => (
                    <tr key={o.id} className="border-t border-gray-100 align-top">
                      <td className="px-3 py-2">
                        <Link href={`/orders/${o.order_number}`} className="text-primary font-medium">
                          {o.display_number ?? o.order_number}
                        </Link>
                        <div className="text-[11px] text-gray-400">{o.order_number}</div>
                      </td>
                      <td className="px-3 py-2">{branchName(o.branch_id)}</td>
                      <td className="px-3 py-2 text-xs">{o.business_date ?? '—'}</td>
                      <td className="px-3 py-2 text-right">{formatCurrency(Number(o.total))}</td>
                      <td className="px-3 py-2">
                        <Badge variant={STATUS_VARIANT[o.pricing_status ?? ''] ?? 'neutral'}>
                          {o.pricing_status ?? '—'}
                        </Badge>
                        {o.pricing_audit && Array.isArray((o.pricing_audit as { differences?: unknown }).differences) && (
                          <details className="mt-1 text-[11px] text-gray-600">
                            <summary className="cursor-pointer">What differed</summary>
                            <ul className="mt-1 space-y-0.5">
                              {((o.pricing_audit as { differences: { field: string; server: string; client: string }[] }).differences).map(d => (
                                <li key={d.field}>
                                  <code>{d.field}</code>: receipt {d.client} · server {d.server}
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {o.ingested_late && <Badge variant="warning">late</Badge>}
                          {o.ingest_flags.map(f => (
                            <Badge key={f} variant="neutral">{f.replace(/_/g, ' ')}</Badge>
                          ))}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">
                        {o.ingested_at ? formatDateTime(o.ingested_at) : '—'}
                      </td>
                    </tr>
                  ))}
                  {data.orders.length === 0 && (
                    <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">Nothing to review.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2 className="text-sm font-body font-medium uppercase tracking-wider text-gray-600 mb-2">
              Quarantined ({data.quarantine.length})
            </h2>
            <div className="space-y-3">
              {data.quarantine.map(q => (
                <div key={q.id} className="border border-red-200 bg-red-50/40 p-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <div className="font-body text-sm">
                      <span className="font-medium">{q.display_number ?? q.id}</span>
                      <span className="text-gray-500"> · {branchName(q.branch_id)}</span>
                      {q.total && <span className="text-gray-500"> · {formatCurrency(Number(q.total))}</span>}
                    </div>
                    <span className="text-xs text-gray-500">{formatDateTime(q.received_at)}</span>
                  </div>
                  <p className="mt-1 text-xs text-red-700 font-mono break-all">{q.error}</p>
                  <details className="mt-1 text-[11px]">
                    <summary className="cursor-pointer text-gray-600">Sale as the till sent it</summary>
                    <pre className="mt-1 max-h-64 overflow-auto bg-white p-2 text-[11px]">
                      {JSON.stringify(q.payload, null, 2)}
                    </pre>
                  </details>
                  {q.resolved_at ? (
                    <p className="mt-2 text-xs text-gray-600">Resolved: {q.resolution_note}</p>
                  ) : (
                    <div className="mt-2 flex flex-wrap items-end gap-2">
                      <div className="min-w-[16rem] flex-1">
                        <Input
                          placeholder="How it was resolved (e.g. re-rung as POS-K001-…)"
                          value={notes[q.id] ?? ''}
                          onChange={e => setNotes(n => ({ ...n, [q.id]: e.target.value }))}
                        />
                      </div>
                      <Button size="sm" variant="outline" onClick={() => void resolve(q)}>
                        Mark resolved
                      </Button>
                    </div>
                  )}
                </div>
              ))}
              {data.quarantine.length === 0 && (
                <p className="text-sm text-gray-400 font-body">Nothing quarantined.</p>
              )}
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
