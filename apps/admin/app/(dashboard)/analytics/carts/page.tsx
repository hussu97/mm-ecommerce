'use client';

/**
 * The baskets people are holding right now.
 *
 * Everything else under Analytics answers a question about the past, off
 * `orders`, and is scoped by a date range. This screen answers the opposite one
 * — what is in people's hands at this moment — so it has no date range at all,
 * and its controls are about *staleness* instead: how long a basket has been
 * sitting, what it is worth, and whether there is anywhere to write about it.
 *
 * That last column is the point. `docs/cart-abandonment-email.md` makes the
 * case for a recovery email off the back of this screen, and the header here is
 * where the case is measured: "reachable" against "sitting there".
 *
 * No arithmetic happens in this file. Every figure — subtotal, small-basket
 * surcharge, the courier's estimate, the total — is quoted by
 * `/analytics/live-carts`, which computes them with the same functions that
 * charge them (CLAUDE.md rule 10).
 */

import { useCallback, useState } from 'react';
import Link from 'next/link';
import { analyticsApi } from '@/lib/api';
import type { LiveCart, LiveCartsSummary } from '@/lib/types';
import { formatCurrency, formatDateTime } from '@/lib/utils';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { Badge, Button, Input, LoadError, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { idleLabel, idleTone } from './format';

// ─── Filters ──────────────────────────────────────────────────────────────────

const IDLE_OPTIONS = [
  { value: '0', label: 'Any age' },
  { value: '30', label: 'Idle 30+ min' },
  { value: '60', label: 'Idle 1+ hour' },
  { value: '360', label: 'Idle 6+ hours' },
  { value: '1440', label: 'Idle 24+ hours' },
];

const REACH_OPTIONS = [
  { value: '', label: 'Everyone' },
  { value: 'yes', label: 'Has an email' },
  { value: 'no', label: 'No email' },
];

// ─── Small pieces ─────────────────────────────────────────────────────────────

function MetricCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 p-4">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">{label}</p>
      <p className="font-display text-2xl text-gray-800">{value}</p>
      {sub && <p className="text-xs font-body text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function Lines({ cart }: { cart: LiveCart }) {
  return (
    <ul className="space-y-1">
      {cart.lines.map((line, i) => (
        <li key={`${line.product_id}-${i}`}>
          <span className="text-gray-800">{line.quantity} × {line.product_name}</span>
          <span className="text-gray-400"> · {formatCurrency(line.line_total)}</span>
          {line.options.length > 0 && (
            <span className="block text-[10px] text-gray-400">{line.options.join(', ')}</span>
          )}
          {line.personalisation_note && (
            <span className="block text-[10px] text-gray-400 italic">
              &ldquo;{line.personalisation_note}&rdquo;
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function LiveCartsPage() {
  const [search, setSearch] = useState('');
  const [reach, setReach] = useState('');
  const [idleMin, setIdleMin] = useState('0');
  const [minValue, setMinValue] = useState('');
  const [summary, setSummary] = useState<LiveCartsSummary | null>(null);

  const debouncedSearch = useDebouncedValue(search);
  const debouncedMinValue = useDebouncedValue(minValue);

  const fetchCarts = useCallback(
    async (page: number, perPage: number) => {
      const res = await analyticsApi.liveCarts({
        page,
        per_page: perPage,
        search: debouncedSearch || undefined,
        has_email: reach === '' ? undefined : reach === 'yes',
        min_value: debouncedMinValue ? Number(debouncedMinValue) : undefined,
        idle_minutes_min: Number(idleMin) || undefined,
      });
      // The header follows the filters, so it is read off the same response
      // rather than fetched separately — two calls could answer for two
      // different sets, and the comparison the header exists to make would stop
      // being a comparison.
      setSummary(res.summary);
      return res;
    },
    [debouncedSearch, reach, debouncedMinValue, idleMin],
  );

  const {
    items, total, pages, page, perPage, setPage, setPerPage, loading, loadError, refetch,
  } = useApiList<LiveCart>({ paginate: 'server', fetch: fetchCarts });

  return (
    <div>
      <LoadError message={loadError} onRetry={refetch} />

      <div className="flex flex-wrap items-start justify-between gap-3 mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Live Baskets</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            Baskets holding items right now, most recently touched first.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="min-h-11 md:min-h-0" disabled={loading} onClick={refetch}>
            Refresh
          </Button>
          <Link
            href="/analytics"
            className="px-3 py-1.5 min-h-11 md:min-h-0 flex items-center text-xs font-body uppercase tracking-wider border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            Analytics
          </Link>
        </div>
      </div>

      {/* The recovery case, in four numbers. "Reachable" against "sitting
          there" is the whole question an abandoned-cart email answers. */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Live baskets" value={String(summary?.carts ?? 0)} sub="matching these filters" />
        <MetricCard label="Basket value" value={formatCurrency(summary?.total_value ?? 0)} sub="goods only" />
        <MetricCard
          label="Reachable"
          value={String(summary?.with_email ?? 0)}
          sub={`${formatCurrency(summary?.reachable_value ?? 0)} we could write about`}
        />
        <MetricCard
          label="Going cold"
          value={String(summary?.idle_over_1h ?? 0)}
          sub={`idle 1h+ · ${summary?.idle_over_24h ?? 0} over a day`}
        />
      </div>

      {/* Filters */}
      <div className="bg-white border border-gray-200 p-4 mb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <Input
          label="Search"
          placeholder="Email or session id"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <Select
          label="Contactable"
          options={REACH_OPTIONS}
          value={reach}
          onChange={e => setReach(e.target.value)}
        />
        <Select
          label="Idle for"
          options={IDLE_OPTIONS}
          value={idleMin}
          onChange={e => setIdleMin(e.target.value)}
        />
        <Input
          label="Worth at least (AED)"
          type="number"
          min={0}
          placeholder="Any"
          value={minValue}
          onChange={e => setMinValue(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<LiveCart>
          rows={items}
          rowKey={c => c.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              No baskets match these filters.
            </p>
          }
          columns={[
            {
              header: 'Shopper',
              priority: 'primary',
              render: c =>
                c.email ? (
                  <span className="text-xs font-body font-medium text-gray-800 break-all">{c.email}</span>
                ) : (
                  <span className="text-xs font-body text-gray-400 italic">No email</span>
                ),
            },
            {
              header: 'Identity',
              priority: 'secondary',
              render: c =>
                c.email ? (
                  <>
                    {/* Where the address came from, because it changes what
                        the basket means: `checkout` is somebody who reached
                        the form and turned back. */}
                    {c.email_source === 'account' ? 'from their account' : 'typed at checkout'}
                    {c.is_registered ? ' · registered' : ''}
                  </>
                ) : (
                  <span className="break-all">
                    {c.session_id ? `session ${c.session_id.slice(0, 16)}…` : '—'}
                  </span>
                ),
            },
            {
              header: 'Items',
              render: c => (
                <>
                  <Lines cart={c} />
                  <p className="text-[10px] text-gray-400 mt-1">{c.item_count} item(s)</p>
                </>
              ),
            },
            {
              header: 'Goods',
              className: 'text-right',
              render: c => <span className="text-gray-800">{formatCurrency(c.subtotal)}</span>,
            },
            {
              header: 'Fees',
              className: 'text-right',
              render: c => (
                <>
                  {c.delivery_fee !== null ? (
                    <span className="block">
                      {formatCurrency(c.delivery_fee)}
                      <span className="block text-[10px] text-gray-400">
                        {/* A courier's estimate against the pin they dropped,
                            not a price anybody has been quoted. */}
                        est. delivery{c.delivery_zone ? ` · ${c.delivery_zone}` : ''}
                      </span>
                    </span>
                  ) : (
                    <span className="block text-gray-400">
                      —<span className="block text-[10px]">no address yet</span>
                    </span>
                  )}
                  {c.low_order_fee > 0 && (
                    <span className="block mt-1">
                      {formatCurrency(c.low_order_fee)}
                      <span className="block text-[10px] text-gray-400">small basket</span>
                    </span>
                  )}
                </>
              ),
            },
            {
              header: 'Promo',
              render: c =>
                c.promo_code ? (
                  <Badge variant="neutral">{c.promo_code}</Badge>
                ) : (
                  <span className="text-gray-400">—</span>
                ),
            },
            {
              header: 'Est. total',
              className: 'text-right',
              render: c => (
                <>
                  <span className="text-gray-800">{formatCurrency(c.estimated_total)}</span>
                  {c.promo_code && (
                    // Stated rather than silently netted off: what a coupon is
                    // worth depends on the basket, the identity and the day,
                    // and the checkout decides it every time it is asked.
                    <span className="block text-[10px] text-gray-400">before promo</span>
                  )}
                </>
              ),
            },
            {
              header: 'Last seen',
              className: 'text-right',
              render: c => (
                <>
                  <span className={idleTone(c.idle_minutes)}>{idleLabel(c.idle_minutes)}</span>
                  {c.last_activity_at && (
                    <span className="block text-[10px] text-gray-400">
                      {formatDateTime(c.last_activity_at)}
                    </span>
                  )}
                </>
              ),
            },
          ]}
        />
      )}

      <Pagination
        page={page}
        pages={pages}
        total={total}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={setPerPage}
        label="baskets"
      />
    </div>
  );
}
