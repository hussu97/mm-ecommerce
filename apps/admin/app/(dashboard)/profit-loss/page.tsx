'use client';

/**
 * Profit & loss: GMV → PC1 → PC2 → PC3, per sales channel, net of VAT.
 *
 * Every number is the API's (`/profit-loss`, built on the same per-order
 * expressions as the orders list's Profit column), so a channel's PC3 here is
 * exactly the sum of that channel's rows there. The filters live in the URL, so
 * a view is shareable and survives a refresh. Orders are dated by the shop day
 * they were created in, the same window the dashboard and orders list use;
 * marketplace period charges (monthly platform fees…) by their statement date.
 */

import { useEffect, useState } from 'react';
import { profitLossApi, type PnlReport } from '@/lib/api';
import { branchesApi, legalEntitiesApi } from '@/lib/pos-api';
import type { Branch, LegalEntity } from '@/lib/pos-types';
import { LoadError, MultiSelect, Spinner } from '@/components/ui';
import { useUrlFilters, type FilterFieldSpec } from '@/lib/list-filters';
import { DATE_PRESETS } from '@/lib/order-filters';
import { cn, formatCurrency } from '@/lib/utils';

const FIELDS: FilterFieldSpec[] = [
  { key: 'from', kind: 'single' },
  { key: 'to', kind: 'single' },
  { key: 'channels', param: 'channel', kind: 'multi' },
  { key: 'branches', param: 'branch', kind: 'multi' },
  { key: 'entities', param: 'legal_entity', kind: 'multi' },
];

type Filters = {
  from: string;
  to: string;
  channels: string[];
  branches: string[];
  entities: string[];
};

const CHANNEL_LABEL: Record<string, string> = {
  total: 'Total',
  counter: 'Counter',
  website_delivery: 'Website delivery',
  website_pickup: 'Store pickup',
  talabat: 'Talabat',
  keeta: 'Keeta',
  noon_food: 'noon Food',
  deliveroo: 'Deliveroo',
  careem: 'Careem',
  other: 'Other',
};

const CHANNEL_OPTIONS = Object.entries(CHANNEL_LABEL)
  .filter(([code]) => code !== 'total' && code !== 'other')
  .map(([value, label]) => ({ value, label }));

type Column = PnlReport['total'];
type Row = {
  label: string;
  value: (c: Column) => number | null;
  pct?: (c: Column) => number | null;
  kind: 'line' | 'cost' | 'credit' | 'sub' | 'detail' | 'result';
  /** Show a detail row even when it is zero everywhere — a zero is the point. */
  always?: boolean;
};

// The statement, top to bottom. Costs are shown as negatives; the sub-lines
// under a cost group are the parts it is made of.
const ROWS: Row[] = [
  { label: 'GMV (items before discounts, incl. VAT)', value: c => c.gmv, kind: 'line' },
  { label: 'Refunds', value: c => c.refunds, kind: 'cost' },
  { label: 'VAT on sales', value: c => c.output_vat, kind: 'cost' },
  { label: 'Net revenue', value: c => c.net_revenue, kind: 'sub' },
  { label: 'COGS (net of VAT)', value: c => c.cogs, kind: 'cost' },
  // By inventory item kind. Always shown: a packaging line at zero is the
  // finding (packaging never priced), not noise to hide.
  { label: 'Produced goods', value: c => c.cogs_produced, kind: 'detail', always: true },
  { label: 'Raw ingredients', value: c => c.cogs_raw, kind: 'detail', always: true },
  { label: 'Packaging', value: c => c.cogs_packaging, kind: 'detail', always: true },
  { label: 'Resale goods', value: c => c.cogs_resale, kind: 'detail', always: true },
  { label: 'PC1', value: c => c.pc1, pct: c => c.pc1_pct, kind: 'sub' },
  { label: 'Delivery fees charged (no VAT)', value: c => c.delivery_fees, kind: 'credit' },
  { label: 'Payment fees', value: c => c.payment_fees, kind: 'cost' },
  { label: 'Aggregator & delivery fees', value: c => c.aggregator_and_delivery_fees, kind: 'cost' },
  { label: 'Commission', value: c => c.commission, kind: 'detail' },
  { label: 'Loyalty / Pro / subsidy fees', value: c => c.marketplace_fees, kind: 'detail' },
  { label: 'Our courier', value: c => c.delivery_cost, kind: 'detail' },
  { label: 'Misc fees', value: c => c.misc_fees, kind: 'cost' },
  { label: 'Cancellation charges', value: c => c.cancellation_charges, kind: 'detail' },
  { label: 'Platform & period charges', value: c => c.period_charges, kind: 'detail' },
  { label: 'VAT reclaimed on fees', value: c => c.fees_vat, kind: 'credit' },
  { label: 'PC2', value: c => c.pc2, pct: c => c.pc2_pct, kind: 'sub' },
  { label: 'Discounts', value: c => c.discounts, kind: 'cost' },
  { label: 'PC3', value: c => c.pc3, pct: c => c.pc3_pct, kind: 'result' },
];

function money(value: number | null, kind: Row['kind']) {
  if (value === null) return '—';
  if (value !== 0 && (kind === 'cost' || kind === 'detail')) return `−${formatCurrency(value)}`;
  if (value !== 0 && kind === 'credit') return `+${formatCurrency(value)}`;
  return formatCurrency(value);
}

function Tile({ label, value, pct }: { label: string; value: number; pct?: number | null }) {
  return (
    <div className="border border-gray-200 bg-white px-4 py-3">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">{label}</p>
      <p
        className={cn(
          'mt-1 font-display text-xl tabular-nums',
          value < 0 ? 'text-red-600' : 'text-gray-800',
        )}
      >
        {formatCurrency(value)}
      </p>
      {pct !== undefined && (
        <p className="text-xs font-body text-gray-400">
          {pct === null ? '—' : `${pct.toFixed(1)}% of GMV`}
        </p>
      )}
    </div>
  );
}

export default function ProfitLossPage() {
  const { filters, patch, hasAny, clearAll } = useUrlFilters<Filters>(FIELDS);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [report, setReport] = useState<PnlReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // No range in the URL ⇒ this month to date, the view the page opens on.
  const fallback = DATE_PRESETS.find(p => p.key === 'this_month')!.range();
  const from = filters.from && filters.to ? filters.from : fallback.from;
  const to = filters.from && filters.to ? filters.to : fallback.to;

  useEffect(() => {
    void branchesApi
      .list()
      .then(rows => setBranches(rows.filter(b => !b.deleted_at)))
      .catch(() => setBranches([]));
    void legalEntitiesApi
      .list()
      .then(setEntities)
      .catch(() => setEntities([]));
  }, []);

  const key = JSON.stringify([from, to, filters.channels, filters.branches, filters.entities]);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError('');
    profitLossApi
      .report({
        date_from: from,
        date_to: to,
        channels: filters.channels.length ? filters.channels : undefined,
        branch_ids: filters.branches.length ? filters.branches : undefined,
        legal_entity_ids: filters.entities.length ? filters.entities : undefined,
      })
      .then(r => live && setReport(r))
      .catch(e => live && setError((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const columns: Column[] = report
    ? report.channels.length > 1
      ? [report.total, ...report.channels]
      : report.channels.length === 1
        ? report.channels
        : [report.total]
    : [];
  const total = report?.total;
  const activePreset = DATE_PRESETS.find(p => {
    const r = p.range();
    return r.from === from && r.to === to;
  })?.key;

  return (
    <div>
      <LoadError message={error} />
      <div className="mb-6">
        <h1 className="font-display text-2xl text-gray-800">Profit &amp; Loss</h1>
        <p className="mt-0.5 text-xs font-body text-gray-400">
          {from === to ? from : `${from} → ${to}`} · revenue and fees as billed, VAT shown as its
          own lines · delivered orders and charged cancellations
        </p>
      </div>

      <div className="mb-6 flex flex-col gap-3 md:flex-row md:flex-wrap md:items-center">
        <div className="flex flex-wrap gap-1.5">
          {DATE_PRESETS.map(p => (
            <button
              key={p.key}
              type="button"
              onClick={() => patch(p.range())}
              aria-pressed={activePreset === p.key}
              className={cn(
                'inline-flex min-h-[var(--tap-min)] items-center border px-2.5 py-1 text-xs font-body md:min-h-0',
                activePreset === p.key
                  ? 'border-primary bg-primary/5 text-primary'
                  : 'border-gray-200 text-gray-600 hover:border-gray-300',
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 text-xs font-body text-gray-500">
          <input
            type="date"
            value={from}
            onChange={e => patch({ from: e.target.value, to })}
            className="border border-gray-200 px-2 py-1"
            aria-label="From"
          />
          <span>→</span>
          <input
            type="date"
            value={to}
            onChange={e => patch({ from, to: e.target.value })}
            className="border border-gray-200 px-2 py-1"
            aria-label="To"
          />
        </div>
        <MultiSelect
          options={CHANNEL_OPTIONS}
          value={filters.channels}
          onChange={channels => patch({ channels })}
          placeholder="All channels"
          className="md:w-48"
        />
        <MultiSelect
          options={branches.map(b => ({ value: b.id, label: `${b.reference} · ${b.name}` }))}
          value={filters.branches}
          onChange={branches => patch({ branches })}
          placeholder="All branches"
          className="md:w-48"
        />
        <MultiSelect
          options={entities.map(e => ({ value: e.id, label: e.brand_name }))}
          value={filters.entities}
          onChange={entities => patch({ entities })}
          placeholder="All entities"
          className="md:w-48"
        />
        {hasAny && (
          <button
            type="button"
            onClick={clearAll}
            className="text-xs font-body text-gray-400 underline hover:text-gray-600"
          >
            Clear
          </button>
        )}
      </div>

      {loading && !report ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : report && total ? (
        <div className={cn('space-y-6', loading && 'opacity-60')}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Tile label="GMV" value={total.gmv} />
            <Tile label="PC1 · after COGS" value={total.pc1} pct={total.pc1_pct} />
            <Tile label="PC2 · after fees" value={total.pc2} pct={total.pc2_pct} />
            <Tile label="PC3 · after discounts" value={total.pc3} pct={total.pc3_pct} />
          </div>

          <div className="overflow-x-auto border border-gray-200 bg-white">
            <table className="w-full min-w-[640px] text-xs font-body">
              <thead>
                <tr className="border-b border-gray-200 text-[11px] uppercase tracking-wider text-gray-400">
                  <th className="sticky left-0 bg-white px-3 py-2 text-left font-normal">Line</th>
                  {columns.map(c => (
                    <th key={c.channel} className="px-3 py-2 text-right font-normal">
                      {CHANNEL_LABEL[c.channel] ?? c.channel}
                      <span className="block normal-case tracking-normal text-gray-300">
                        {c.orders} orders
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.filter(
                  r =>
                    r.kind !== 'detail' || r.always || columns.some(c => (r.value(c) ?? 0) !== 0),
                ).map(r => {
                  return (
                    <tr
                      key={r.label}
                      className={cn(
                        'border-b border-gray-100 last:border-0',
                        (r.kind === 'sub' || r.kind === 'result') && 'bg-gray-50 font-medium text-gray-800',
                        r.kind === 'result' && 'text-sm',
                        r.kind === 'detail' && 'text-[11px] text-gray-400',
                        (r.kind === 'line' || r.kind === 'cost' || r.kind === 'credit') && 'text-gray-600',
                      )}
                    >
                      <td
                        className={cn(
                          'sticky left-0 px-3 py-1.5',
                          r.kind === 'sub' || r.kind === 'result' ? 'bg-gray-50' : 'bg-white',
                          r.kind === 'detail' && 'pl-6',
                        )}
                      >
                        {r.label}
                      </td>
                      {columns.map(c => {
                        const v = r.value(c);
                        const pct = r.pct?.(c);
                        return (
                          <td
                            key={c.channel}
                            className={cn(
                              'whitespace-nowrap px-3 py-1.5 text-right tabular-nums',
                              r.kind === 'result' && v !== null && v < 0 && 'text-red-600',
                            )}
                          >
                            {money(v, r.kind)}
                            {pct !== undefined && (
                              <span className="block text-[10px] text-gray-400">
                                {pct === null ? '' : `${pct.toFixed(1)}%`}
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <section className="border border-gray-200 bg-white p-4">
              <h2 className="mb-2 font-display text-base text-gray-800">VAT</h2>
              <p className="mb-3 text-[11px] font-body text-gray-400">
                The two VAT lines in the statement. VAT on the stock sold isn&apos;t here: it was
                reclaimed in the return for the period it was bought (see the VAT report), so COGS
                is shown at net cost.
              </p>
              <dl className="space-y-1 text-xs font-body">
                <div className="flex justify-between text-gray-600">
                  <dt>VAT on sales (after refunds)</dt>
                  <dd className="whitespace-nowrap tabular-nums">{formatCurrency(report.vat.output_vat)}</dd>
                </div>
                <div className="flex justify-between text-gray-600">
                  <dt>VAT reclaimed on fees &amp; charges</dt>
                  <dd className="whitespace-nowrap tabular-nums">−{formatCurrency(report.vat.fees_vat_reclaimed)}</dd>
                </div>
                <div className="flex justify-between border-t border-gray-200 pt-1 font-medium text-gray-800">
                  <dt>Net VAT on these sales</dt>
                  <dd className="whitespace-nowrap tabular-nums">{formatCurrency(report.vat.net_vat)}</dd>
                </div>
              </dl>
            </section>

            <section className="border border-gray-200 bg-white p-4">
              <h2 className="mb-2 font-display text-base text-gray-800">Platform &amp; period charges</h2>
              {!report.period_charges_included ? (
                <p className="text-xs font-body text-gray-400">
                  Left out for this selection — marketplaces bill these per account, not per
                  branch, and to the entity their orders are booked under.
                </p>
              ) : report.period_charges.length === 0 ? (
                <p className="text-xs font-body text-gray-400">
                  None on a statement dated in this window.
                </p>
              ) : (
                <ul className="space-y-1 text-xs font-body">
                  {report.period_charges.map(c => (
                    <li key={`${c.channel}-${c.category}`} className="flex justify-between gap-3 text-gray-600">
                      <span>
                        {CHANNEL_LABEL[c.channel] ?? c.channel} · {c.description ?? c.category}
                        {c.is_true_up && (
                          <span
                            className="text-gray-400"
                            title="The statement's fee less what its orders already carry"
                          >
                            {' '}
                            (not on orders)
                          </span>
                        )}
                        <span className="block text-[11px] text-gray-400">
                          {c.first_date === c.last_date ? c.first_date : `${c.first_date} → ${c.last_date}`}
                        </span>
                      </span>
                      <span className="whitespace-nowrap tabular-nums">
                        {c.amount < 0 ? formatCurrency(-c.amount) : `−${formatCurrency(c.amount)}`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-[11px] font-body text-gray-400">
                Dated by the statement they arrive on, as billed (VAT included).
              </p>
            </section>
          </div>

          <section className="border border-amber-200 bg-amber-50 p-4 text-xs font-body text-amber-800 space-y-1">
            <p>
              <strong>COGS</strong> is recorded on {`${total.orders_with_cogs} of ${total.orders} orders`} —
              the rest drew no stock (before their branch&apos;s inventory go-live, or never posted)
              and count as unknown, not free, so PC1 is overstated by their cost.
              {total.cogs_provisional > 0 &&
                ` ${formatCurrency(total.cogs_provisional)} of COGS is priced provisionally until a purchase order prices the stock.`}
            </p>
            {total.orders_fees_pending > 0 && (
              <p>
                <strong>{total.orders_fees_pending} orders</strong> are still waiting on a fee — a
                marketplace commission before its statement lands, or a courier invoice — so PC2
                will fall as they arrive.
              </p>
            )}
            {total.charged_cancellations > 0 && (
              <p>
                Includes {total.charged_cancellations} cancelled orders the marketplace still charged
                for (no revenue, only the charge).
              </p>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}
