'use client';

import { useCallback, useState } from 'react';

import { Badge, LoadError, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { useApiList } from '@/hooks/useApiList';
import { aggregatorRunsApi } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import type { Schemas } from '@mm/types';

import { AggregatorTabs } from '../AggregatorTabs';

// The generated contract (rule 8).
type RunRow = Schemas['AggregatorSyncRunOut'];

/**
 * The aggregator ingest run trail.
 *
 * Every time the scraper runs — the nightly pass, or a manual re-run over a date
 * range — it records one row per channel here: when it ran, over what dates,
 * whether it succeeded (and, when it didn't, why), what it pulled back
 * (orders/statements/payouts/invoices) and how much of that promoted into an MM
 * order (new vs already-existing vs not-promoted). The point of the screen is to
 * see at a glance which marketplaces are healthy and which need a re-login.
 */

const CHANNEL_OPTIONS = [
  { value: '', label: 'All channels' },
  { value: 'careem', label: 'Careem' },
  { value: 'deliveroo', label: 'Deliveroo' },
  { value: 'talabat', label: 'Talabat' },
  { value: 'noon', label: 'noon' },
  { value: 'keeta', label: 'Keeta' },
];

const MODE_OPTIONS = [
  { value: '', label: 'All modes' },
  { value: 'sales', label: 'Sales' },
  { value: 'finance', label: 'Finance' },
  { value: 'backfill', label: 'Backfill' },
];

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'completed', label: 'Completed' },
  { value: 'partial', label: 'Partial' },
  { value: 'failed', label: 'Failed' },
  { value: 'running', label: 'Running' },
  { value: 'planned', label: 'Planned' },
];

const STATUS_TONE: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'neutral'> = {
  completed: 'success',
  partial: 'warning',
  failed: 'danger',
  running: 'info',
  planned: 'neutral',
};

function channelName(c: string): string {
  return CHANNEL_OPTIONS.find(o => o.value === c)?.label ?? c;
}

function num(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : String(n);
}

function pct(n: number | null | undefined): string {
  return n === null || n === undefined ? '' : ` (${n}%)`;
}

export default function AggregatorRunsPage() {
  const [channel, setChannel] = useState('');
  const [mode, setMode] = useState('');
  const [status, setStatus] = useState('');

  // Server-side filtering: a filter change is a new fetcher identity, which the
  // hook treats as "filters changed" and resets to page 1 + refetches.
  const fetchRows = useCallback(
    async (page: number, perPage: number) => {
      const res = await aggregatorRunsApi.list({
        channel: channel || undefined,
        mode: mode || undefined,
        status: status || undefined,
        limit: perPage,
        offset: (page - 1) * perPage,
      });
      return {
        items: res.items,
        total: res.total,
        pages: Math.max(1, Math.ceil(res.total / perPage)),
      };
    },
    [channel, mode, status],
  );

  const {
    items: rows, total, pages, page, perPage, setPage, setPerPage, loading, loadError, refetch,
  } = useApiList<RunRow>({ paginate: 'server', fetch: fetchRows });

  const columns: DataColumn<RunRow>[] = [
    {
      header: 'When',
      priority: 'primary',
      render: r => (
        <div>
          <div className="font-medium">{r.started_at ? formatDateTime(r.started_at) : '—'}</div>
          <div className="text-xs text-gray-500">
            {r.from_date && r.to_date
              ? r.from_date === r.to_date
                ? r.from_date
                : `${r.from_date} → ${r.to_date}`
              : 'daily window'}
          </div>
        </div>
      ),
    },
    {
      header: 'Channel',
      render: r => <Badge variant="neutral">{channelName(r.channel)}</Badge>,
    },
    {
      header: 'Mode',
      priority: 'secondary',
      render: r => <span className="text-gray-600 capitalize">{r.mode}</span>,
    },
    {
      header: 'Status',
      render: r => <Badge variant={STATUS_TONE[r.status] ?? 'neutral'}>{r.status}</Badge>,
    },
    {
      header: 'Retrieved',
      priority: 'secondary',
      render: r => {
        const modes = (r.stats?.modes ?? {}) as Record<string, Record<string, number>>;
        const sales = modes.sales?.orders ?? modes.sales?.written;
        const stmts = r.statements_total;
        const pays = r.payouts_total;
        const inv = r.invoices_total;
        return (
          <div className="text-xs text-gray-600 space-y-0.5">
            {sales !== undefined && <div>{sales} orders</div>}
            {(stmts ?? 0) > 0 && <div>{stmts} statements</div>}
            {(pays ?? 0) > 0 && <div>{pays} payouts</div>}
            {(inv ?? 0) > 0 && <div>{inv} invoices</div>}
            {sales === undefined && !stmts && !pays && <div className="text-gray-400">—</div>}
          </div>
        );
      },
    },
    {
      header: 'Promotion',
      priority: 'secondary',
      render: r => {
        if (r.orders_retrieved === null || r.orders_retrieved === undefined) {
          return <span className="text-gray-400 text-xs">—</span>;
        }
        return (
          <div className="text-xs text-gray-600 space-y-0.5">
            <div>
              <span className="font-medium text-gray-800">{num(r.orders_promoted)}</span>
              /{num(r.orders_retrieved)} promoted{pct(r.pct_promoted)}
            </div>
            <div className="text-gray-500">
              {num(r.orders_promoted_new)} new · {num(r.orders_promoted_existing)} existing ·{' '}
              {num(r.orders_not_promoted)} not
            </div>
          </div>
        );
      },
    },
    {
      header: 'Reason',
      render: r =>
        r.error ? (
          <span className="text-xs text-red-600 break-words">{r.error}</span>
        ) : (
          <span className="text-gray-300">—</span>
        ),
    },
  ];

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div>
        <h1 className="font-display text-2xl text-gray-800">Runs</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          {total} run{total === 1 ? '' : 's'} · the ingest trail for every scrape
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <div className="w-44">
          <Select value={channel} onChange={e => setChannel(e.target.value)} options={CHANNEL_OPTIONS} />
        </div>
        <div className="w-40">
          <Select value={mode} onChange={e => setMode(e.target.value)} options={MODE_OPTIONS} />
        </div>
        <div className="w-44">
          <Select value={status} onChange={e => setStatus(e.target.value)} options={STATUS_OPTIONS} />
        </div>
      </div>

      {loadError && <LoadError message={loadError} onRetry={refetch} />}

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <>
          <DataTable<RunRow>
            columns={columns}
            rows={rows}
            rowKey={r => r.id}
            rowClassName={r => (r.status === 'failed' ? 'bg-red-50/60' : undefined)}
            empty={
              <p className="py-16 text-center text-sm text-gray-400 font-body">
                No runs recorded yet.
              </p>
            }
          />
          <Pagination
            page={page}
            pages={pages}
            total={total}
            perPage={perPage}
            onPageChange={setPage}
            onPerPageChange={size => {
              setPerPage(size);
              setPage(1);
            }}
            label="runs"
          />
        </>
      )}
    </div>
  );
}
