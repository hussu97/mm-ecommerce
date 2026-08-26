'use client';

import { useCallback, useEffect, useState } from 'react';

import { reconciliationApi } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type {
  ReconRow,
  ReconMatchStatus,
  ReconSummary,
  ReconSummaryRow,
} from '@/lib/types';
import type { Branch } from '@/lib/pos-types';
import { Badge, LoadError, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { useApiList } from '@/hooks/useApiList';
import { formatCurrency, formatDateTime } from '@/lib/utils';
import { AggregatorTabs } from '../AggregatorTabs';

/**
 * Where the aggregator's books and ours are reconciled.
 *
 * Every aggregator order the maker-checker pass looked at, one row: the
 * commission each marketplace charged against what its rate card says it should
 * have, the refund each side recorded, and whether the two lined up at all. The
 * point of the screen is the disagreements — a non-zero commission variance, a
 * refund one side booked and the other did not, an order that matched on neither
 * side — so those are what the table highlights and the "flagged only" toggle
 * narrows to.
 */

// The five marketplaces the reconciler knows. `''` is "all channels" — an empty
// select value the query builder drops rather than sending, same as everywhere.
const CHANNEL_OPTIONS = [
  { value: '', label: 'All channels' },
  { value: 'careem', label: 'Careem' },
  { value: 'deliveroo', label: 'Deliveroo' },
  { value: 'talabat', label: 'Talabat' },
  { value: 'noon', label: 'noon' },
  { value: 'keeta', label: 'Keeta' },
];

const MATCH_STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'matched', label: 'Matched' },
  { value: 'unmatched_agg', label: 'Unmatched (aggregator)' },
  { value: 'unmatched_mm', label: 'Unmatched (MM)' },
  { value: 'no_maker_side', label: 'No maker side' },
];

const MATCH_LABELS: Record<ReconMatchStatus, string> = {
  matched: 'Matched',
  unmatched_agg: 'Unmatched agg',
  unmatched_mm: 'Unmatched MM',
  no_maker_side: 'No maker side',
};

const MATCH_VARIANTS: Record<ReconMatchStatus, 'success' | 'warning' | 'danger' | 'neutral'> = {
  matched: 'success',
  unmatched_agg: 'warning',
  unmatched_mm: 'warning',
  no_maker_side: 'danger',
};

/** Prettify a channel code for a card title without a lookup table. */
function channelName(code: string): string {
  return code === 'noon' ? 'noon' : code.charAt(0).toUpperCase() + code.slice(1);
}

/**
 * A rate as a percent, whichever way the API encoded it.
 *
 * A commission rate can arrive as a fraction (`0.15`) or as a percentage
 * already (`15`); both mean the same thing and both should read "15.0%". A
 * value at or below 1 is taken as a fraction and scaled, anything larger is
 * taken as already a percentage.
 */
function formatRate(value: number | null | undefined): string {
  if (value == null) return '—';
  const pct = Math.abs(value) <= 1 ? value * 100 : value;
  return `${pct.toFixed(1)}%`;
}

/**
 * A row is worth a second look when the server raised a flag on it. This mirrors
 * the API's `_flagged_clause` exactly (item/refund flag, or a non-empty `flags`
 * — which now carries commission_variance, amount_variance, refund and no_mm_order
 * codes), so the red highlight, the "Flagged only" filter and the summary counts
 * all agree. Keying off `match_status !== 'matched'` used to redden every
 * `no_maker_side` row (the expected state for aggregator-only branches) and off a
 * bare `amount_variance !== 0` reddened sub-tolerance rounding noise that then
 * vanished under the filter.
 */
function isFlagged(r: ReconRow): boolean {
  return r.item_flag || r.refund_flag || (r.flags?.length ?? 0) > 0;
}

function StatCard({
  label, value, sub, tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'default' | 'warning' | 'danger';
}) {
  const valueColor =
    tone === 'danger' ? 'text-red-600' : tone === 'warning' ? 'text-amber-600' : 'text-gray-800';
  return (
    <div className="bg-white border border-gray-200 p-4">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">{label}</p>
      <p className={`font-display text-2xl ${valueColor}`}>{value}</p>
      {sub && <p className="text-xs font-body text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function ReconciliationPage() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [channel, setChannel] = useState('');
  const [branchId, setBranchId] = useState('');
  const [matchStatus, setMatchStatus] = useState('');
  const [flaggedOnly, setFlaggedOnly] = useState(false);

  const [summary, setSummary] = useState<ReconSummary | null>(null);
  const [summaryError, setSummaryError] = useState('');

  useEffect(() => {
    void branchesApi
      .list()
      .then(rows => setBranches(rows.filter(b => !b.deleted_at)))
      .catch(() => setBranches([]));
  }, []);

  // The stat cards answer to channel and branch, but not to match-status or the
  // flagged toggle — those narrow the table, and a roll-up that moved with them
  // would stop being the "here is everything" number the cards are for.
  const loadSummary = useCallback(async () => {
    try {
      const data = await reconciliationApi.summary({
        channel: channel || undefined,
        branch_id: branchId || undefined,
      });
      setSummary(data);
      setSummaryError('');
    } catch (e) {
      setSummary(null);
      setSummaryError(e instanceof Error ? e.message : 'Could not load the summary');
    }
  }, [channel, branchId]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  // Server-side pagination by limit/offset. `useApiList` speaks page/perPage, so
  // the fetcher turns the page into an offset and the `{ items, total }` answer
  // into the `{ items, total, pages }` the hook expects.
  const fetchRows = useCallback(
    async (page: number, perPage: number) => {
      const res = await reconciliationApi.list({
        channel: channel || undefined,
        branch_id: branchId || undefined,
        match_status: matchStatus || undefined,
        flagged: flaggedOnly || undefined,
        limit: perPage,
        offset: (page - 1) * perPage,
      });
      return {
        items: res.items,
        total: res.total,
        pages: Math.max(1, Math.ceil(res.total / perPage)),
      };
    },
    [channel, branchId, matchStatus, flaggedOnly],
  );

  const {
    items, total, pages, page, perPage, setPage, setPerPage, loading, loadError, refetch,
  } = useApiList<ReconRow>({ paginate: 'server', fetch: fetchRows });

  const columns: DataColumn<ReconRow>[] = [
    {
      header: 'Order',
      priority: 'primary',
      render: r => (
        <div>
          <div className="font-medium">{r.external_order_id ?? '—'}</div>
          <div className="font-mono text-xs text-gray-500">
            {r.mm_order_id ? `MM ${r.mm_order_id}` : 'no MM order'}
          </div>
        </div>
      ),
    },
    {
      header: 'Channel',
      render: r => <Badge variant="neutral">{channelName(r.channel)}</Badge>,
    },
    {
      header: 'Branch',
      priority: 'secondary',
      render: r => <span className="text-gray-600">{r.branch_name ?? '—'}</span>,
    },
    {
      header: 'Match',
      render: r => (
        <Badge variant={MATCH_VARIANTS[r.match_status]}>{MATCH_LABELS[r.match_status]}</Badge>
      ),
    },
    {
      header: 'Commission (exp / act / var)',
      className: 'text-right whitespace-nowrap',
      render: r => {
        const variance = r.commission_variance;
        const off = variance != null && variance !== 0;
        return (
          <div className="text-right tabular-nums">
            <div className="text-gray-700">
              {money(r.commission_expected)} <span className="text-gray-300">/</span>{' '}
              {money(r.commission_actual)}
            </div>
            <div className={off ? 'text-xs font-medium text-red-600' : 'text-xs text-gray-400'}>
              {variance == null ? '—' : `${variance > 0 ? '+' : ''}${formatCurrency(variance)}`}
            </div>
          </div>
        );
      },
    },
    {
      header: 'Eff. rate',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-600">{formatRate(r.commission_rate_effective)}</span>,
    },
    {
      header: 'Refund (agg / MM)',
      className: 'text-right whitespace-nowrap',
      render: r => {
        const mismatch =
          r.refund_flag || (r.refund_agg ?? 0) !== (r.refund_mm ?? 0);
        return (
          <div className={`text-right tabular-nums ${mismatch ? 'text-red-600 font-medium' : 'text-gray-600'}`}>
            {money(r.refund_agg)} <span className="text-gray-300">/</span> {money(r.refund_mm)}
          </div>
        );
      },
    },
    {
      header: 'Item',
      render: r =>
        r.item_flag ? <Badge variant="warning">flag</Badge> : <span className="text-gray-300">—</span>,
    },
    {
      header: 'Flags',
      render: r =>
        r.flags && r.flags.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {r.flags.map(f => (
              <Badge key={f} variant="danger">{f}</Badge>
            ))}
          </div>
        ) : (
          <span className="text-gray-300">—</span>
        ),
    },
    {
      header: 'Reconciled',
      render: r =>
        r.reconciled_at ? (
          <span className="text-xs text-gray-500">{formatDateTime(r.reconciled_at)}</span>
        ) : (
          <span className="text-xs text-gray-400">pending</span>
        ),
    },
  ];

  const cards = summaryCards(summary);

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div>
        <h1 className="font-display text-2xl text-gray-800">Reconciliation</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          {total} order{total === 1 ? '' : 's'} · the aggregators&rsquo; books against ours
        </p>
      </div>

      {summaryError && <LoadError message={summaryError} onRetry={loadSummary} />}

      {/* Summary — per-channel roll-up and a grand total. */}
      {summary && cards.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          {cards.map(c => (
            <StatCard key={c.label} label={c.label} value={c.value} sub={c.sub} tone={c.tone} />
          ))}
        </div>
      )}

      {/* Filters. */}
      <div className="flex flex-wrap gap-3">
        <div className="w-44">
          <Select value={channel} onChange={e => setChannel(e.target.value)} options={CHANNEL_OPTIONS} />
        </div>
        <div className="w-52">
          <Select
            value={branchId}
            onChange={e => setBranchId(e.target.value)}
            options={branches.map(b => ({ value: b.id, label: `${b.reference} · ${b.name}` }))}
            placeholder="All branches"
          />
        </div>
        <div className="w-52">
          <Select
            value={matchStatus}
            onChange={e => setMatchStatus(e.target.value)}
            options={MATCH_STATUS_OPTIONS}
          />
        </div>
        <label className="inline-flex items-center gap-2 px-3 min-h-11 md:min-h-0 border border-gray-300 bg-white cursor-pointer select-none">
          <input
            type="checkbox"
            checked={flaggedOnly}
            onChange={e => setFlaggedOnly(e.target.checked)}
            className="accent-primary"
          />
          <span className="text-xs font-body uppercase tracking-wider text-gray-600">Flagged only</span>
        </label>
      </div>

      {loadError && <LoadError message={loadError} onRetry={refetch} />}

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <>
          <DataTable<ReconRow>
            columns={columns}
            rows={items}
            rowKey={r => r.id}
            rowClassName={r => (isFlagged(r) ? 'bg-red-50/60' : undefined)}
            empty={
              <p className="py-16 text-center text-sm text-gray-400 font-body">
                No reconciliation rows for these filters.
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
            label="orders"
          />
        </>
      )}
    </div>
  );
}

/** A money field that is allowed to be absent — a dash rather than "AED 0.00". */
function money(value: number | null | undefined): string {
  return value == null ? '—' : formatCurrency(value);
}

/** Turn the summary into the row of stat cards, totals first. */
function summaryCards(
  summary: ReconSummary | null,
): Array<{ label: string; value: string; sub?: string; tone?: 'default' | 'warning' | 'danger' }> {
  if (!summary) return [];
  const t: ReconSummaryRow = summary.totals;
  const flagged = t.item_flags + t.refund_flags + t.commission_variance_count;
  return [
    { label: 'Orders', value: String(t.total), sub: `${t.matched} matched` },
    {
      label: 'Unmatched (agg)',
      value: String(t.unmatched_agg),
      tone: t.unmatched_agg > 0 ? 'warning' : 'default',
    },
    {
      label: 'No maker side',
      value: String(t.no_maker_side),
      tone: t.no_maker_side > 0 ? 'danger' : 'default',
    },
    {
      label: 'Commission var.',
      value: String(t.commission_variance_count),
      tone: t.commission_variance_count > 0 ? 'danger' : 'default',
      sub: 'orders off rate',
    },
    { label: 'Flagged', value: String(flagged), tone: flagged > 0 ? 'warning' : 'default', sub: 'item · refund · commission' },
    {
      label: 'Commission paid',
      value: formatCurrency(t.commission_actual_sum),
      sub: `avg ${formatRate(t.avg_rate_effective)}`,
    },
  ];
}
