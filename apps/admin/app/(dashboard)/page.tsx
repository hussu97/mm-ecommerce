'use client';

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { dashboardApi, ordersApi } from '@/lib/api';
import type { DashboardToday, DashboardBreakdownRow, Order } from '@/lib/types';
import { Badge, LoadError } from '@/components/ui';
import { formatCurrency, formatTime, formatTimeAgo, cn } from '@/lib/utils';

/** How often the live figures refetch themselves, in ms. */
const REFRESH_MS = 60_000;

const STATUS_BADGE: Record<string, 'warning' | 'info' | 'success' | 'danger' | 'neutral'> = {
  created: 'warning',
  confirmed: 'info',
  arrived_at_pos: 'info',
  packed: 'success',
  out_for_delivery: 'info',
  delivered: 'success',
  undelivered: 'danger',
  cancelled: 'danger',
  payment_failed: 'danger',
  refunded: 'neutral',
  disputed: 'danger',
};

type Growth = number | undefined;

function GrowthPill({ value, title }: { value: Growth; title: string }) {
  if (value === undefined || value === 0) return null;
  const up = value > 0;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 text-[11px] font-body',
        up ? 'text-green-600' : 'text-red-500',
      )}
      title={title}
    >
      <span className="material-icons text-[13px]">{up ? 'arrow_upward' : 'arrow_downward'}</span>
      {Math.abs(value)}%
    </span>
  );
}

function MetricCard({
  label,
  value,
  icon,
  href,
  growth,
  growthTitle,
  loading,
}: {
  label: string;
  value: string;
  icon: string;
  href: string;
  growth?: Growth;
  growthTitle: string;
  loading?: boolean;
}) {
  return (
    <Link
      href={href}
      className="bg-white border border-gray-200 p-4 hover:border-primary transition-colors group"
    >
      <div className="flex items-start justify-between mb-3">
        <span className="material-icons text-secondary text-xl group-hover:text-primary transition-colors">
          {icon}
        </span>
        {!loading && <GrowthPill value={growth} title={growthTitle} />}
      </div>
      <div className="font-display text-2xl text-gray-800 mb-1">{loading ? '—' : value}</div>
      <div className="text-[11px] font-body uppercase tracking-widest text-gray-400">{label}</div>
    </Link>
  );
}

/** The raw status enum value behind a title-cased breakdown label. */
function statusKey(label: string): string {
  return label.toLowerCase().replace(/ /g, '_');
}

function AttentionTile({
  label,
  count,
  icon,
  href,
  tone = 'neutral',
  sub,
}: {
  label: string;
  count: number;
  icon: string;
  href: string;
  tone?: 'neutral' | 'warning' | 'danger';
  sub?: string;
}) {
  const active = count > 0;
  const toneCls =
    active && tone === 'danger'
      ? 'text-red-600'
      : active && tone === 'warning'
        ? 'text-amber-600'
        : 'text-gray-800';
  return (
    <Link
      href={href}
      className={cn(
        'bg-white border p-3 flex items-center gap-3 transition-colors hover:border-primary',
        active && tone === 'danger'
          ? 'border-red-200'
          : active && tone === 'warning'
            ? 'border-amber-200'
            : 'border-gray-200',
      )}
    >
      <span className={cn('material-icons text-lg', active ? toneCls : 'text-gray-300')}>{icon}</span>
      <div className="min-w-0">
        <div className={cn('font-display text-lg leading-none', toneCls)}>{count}</div>
        <div className="text-[10px] font-body uppercase tracking-widest text-gray-400 mt-1 truncate">
          {sub ?? label}
        </div>
      </div>
    </Link>
  );
}

function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-8">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  );
}

/** A labelled share bar — orders width proportional to the row's share. */
function BreakdownBars({ rows, empty }: { rows: DashboardBreakdownRow[]; empty: string }) {
  if (rows.length === 0) {
    return <p className="text-xs text-gray-400 font-body py-3">{empty}</p>;
  }
  const max = Math.max(...rows.map((r) => r.orders), 1);
  return (
    <div className="space-y-2.5">
      {rows.map((r) => (
        <div key={r.label}>
          <div className="flex items-center justify-between text-xs font-body mb-1">
            <span className="text-gray-600">{r.label}</span>
            <span className="text-gray-400">
              {r.orders} · <span className="text-gray-600">{formatCurrency(r.revenue)}</span>
            </span>
          </div>
          <div className="h-1.5 bg-gray-100">
            <div className="h-full bg-secondary" style={{ width: `${(r.orders / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardToday | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);

  // Filters. Empty dates ⇒ the live current day; both set ⇒ that range. `statuses`
  // narrows every figure to the picked order statuses. `search` finds orders by MM
  // number, marketplace ref, or customer.
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [statuses, setStatuses] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');

  const isRange = Boolean(fromDate && toDate);
  const isLive = !fromDate && !toDate;
  const searching = debouncedSearch.length > 0;

  // Debounce the search box so a keystroke is not a request.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(id);
  }, [search]);

  const load = useCallback(async () => {
    // A one-sided date range is ignored (treated as live) until both ends are set.
    const rangeReady = Boolean(fromDate) === Boolean(toDate);
    const [today, ordersRes] = await Promise.allSettled([
      dashboardApi.today({
        date_from: rangeReady ? fromDate || undefined : undefined,
        date_to: rangeReady ? toDate || undefined : undefined,
        statuses: statuses.length ? statuses : undefined,
      }),
      ordersApi.listAll({ per_page: 8, search: debouncedSearch || undefined }),
    ]);

    if (today.status === 'fulfilled') {
      setData(today.value);
      setFailed(false);
    } else {
      setFailed(true);
    }
    if (ordersRes.status === 'fulfilled') setOrders(ordersRes.value.items);

    setRefreshedAt(new Date().toISOString());
    setLoading(false);
  }, [fromDate, toDate, statuses, debouncedSearch]);

  // Re-run whenever a filter changes. The 60s live refresh runs only for the
  // current-day view — a historical range does not move, so polling it is noise.
  useEffect(() => {
    void load();
    if (!isLive) return;
    const id = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(id);
  }, [load, isLive]);

  const toggleStatus = (key: string) =>
    setStatuses(prev => (prev.includes(key) ? prev.filter(s => s !== key) : [...prev, key]));

  const s = data?.summary;
  const ops = data?.ops;
  const growthTitle = isRange ? 'vs the preceding period' : 'vs the same time yesterday';
  const rangeLabel = isRange
    ? data?.business_date_to && data.business_date !== data.business_date_to
      ? `${data.business_date} → ${data.business_date_to}`
      : data?.business_date
    : null;

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Dashboard</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            {isRange && rangeLabel ? (
              <span>Showing {rangeLabel}</span>
            ) : (
              <>
                {new Date().toLocaleDateString('en-AE', {
                  weekday: 'long',
                  day: 'numeric',
                  month: 'long',
                  year: 'numeric',
                })}
                {data && <span className="text-gray-300"> · trading day {data.business_date}</span>}
              </>
            )}
          </p>
        </div>
        <button
          onClick={() => void load()}
          className="shrink-0 inline-flex items-center gap-1.5 text-[11px] font-body uppercase tracking-widest text-gray-500 hover:text-primary transition-colors min-h-11 md:min-h-0"
          title="Refresh"
        >
          <span className="material-icons text-[15px]">refresh</span>
          {refreshedAt ? `As of ${formatTime(refreshedAt)}` : 'Refresh'}
        </button>
      </div>

      {/* Range + search controls. Clearing the dates returns to the live day. */}
      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">From</label>
          <input
            type="date"
            value={fromDate}
            max={toDate || undefined}
            onChange={e => setFromDate(e.target.value)}
            className="px-3 h-10 border border-gray-300 bg-white text-sm font-body outline-none focus:border-primary"
          />
        </div>
        <div>
          <label className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">To</label>
          <input
            type="date"
            value={toDate}
            min={fromDate || undefined}
            onChange={e => setToDate(e.target.value)}
            className="px-3 h-10 border border-gray-300 bg-white text-sm font-body outline-none focus:border-primary"
          />
        </div>
        {!isLive && (
          <button
            onClick={() => {
              setFromDate('');
              setToDate('');
            }}
            className="h-10 px-3 text-xs font-body uppercase tracking-wider text-gray-500 border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Live today
          </button>
        )}
        <div className="flex-1 min-w-[220px]">
          <label className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">Search</label>
          <div className="relative">
            <span className="material-icons absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 text-[18px]">search</span>
            <input
              type="search"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="MM number, marketplace ref, customer name or email"
              className="w-full pl-9 pr-3 h-10 border border-gray-300 bg-white text-sm font-body outline-none focus:border-primary"
            />
          </div>
        </div>
      </div>

      {statuses.length > 0 && (
        <div className="mb-6 flex items-center gap-2 text-xs font-body text-gray-500">
          <span className="uppercase tracking-widest text-gray-400">Filtered to</span>
          {statuses.map(k => (
            <button
              key={k}
              onClick={() => toggleStatus(k)}
              className="inline-flex items-center gap-1 px-2 py-0.5 bg-primary/10 text-primary border border-primary/30"
              title="Remove this status"
            >
              {k.replace(/_/g, ' ')}
              <span className="material-icons text-[13px]">close</span>
            </button>
          ))}
          <button onClick={() => setStatuses([])} className="text-gray-400 hover:text-primary underline">
            clear
          </button>
        </div>
      )}

      {failed && (
        <div className="mb-6">
          <LoadError message="Could not load the figures." onRetry={() => void load()} />
        </div>
      )}

      {/* Headline figures — every order in the window, aggregated server-side */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <MetricCard
          label={isRange ? 'Revenue' : 'Revenue Today'}
          value={formatCurrency(s?.revenue ?? 0)}
          icon="payments"
          href="/orders"
          growth={s?.revenue_growth}
          growthTitle={growthTitle}
          loading={loading}
        />
        <MetricCard
          label={isRange ? 'Orders' : 'Orders Today'}
          value={String(s?.orders ?? 0)}
          icon="receipt_long"
          href="/orders"
          growth={s?.orders_growth}
          growthTitle={growthTitle}
          loading={loading}
        />
        <MetricCard
          label="Delivered"
          value={String(s?.delivered ?? 0)}
          icon="check_circle"
          href="/orders"
          growthTitle={growthTitle}
          loading={loading}
        />
        <MetricCard
          label="Avg Order"
          value={formatCurrency(s?.avg_order_value ?? 0)}
          icon="trending_up"
          href="/analytics"
          growthTitle={growthTitle}
          loading={loading}
        />
      </div>

      {/* Needs attention — the open operational work, right now */}
      <Section title="Needs Attention">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <AttentionTile label="Out for delivery" sub="Out for delivery" count={ops?.out_for_delivery ?? 0} icon="local_shipping" href="/orders" tone="warning" />
          <AttentionTile label="Undelivered" sub="Undelivered" count={ops?.undelivered ?? 0} icon="error_outline" href="/orders" tone="danger" />
          <AttentionTile label="Payment failed" sub="Payment failed" count={ops?.payment_failed_today ?? 0} icon="credit_card_off" href="/orders" tone="danger" />
          <AttentionTile label="Refunds today" sub={ops ? `Refunds · ${formatCurrency(ops.refunds_amount_today)}` : 'Refunds today'} count={ops?.refunds_today ?? 0} icon="undo" href="/orders" tone="warning" />
          <AttentionTile label="Custom due today" sub="Custom due today" count={ops?.custom_orders_due_today ?? 0} icon="cake" href="/custom-orders" tone="warning" />
          <AttentionTile label="Open custom orders" sub="Open custom orders" count={ops?.open_custom_orders ?? 0} icon="pending_actions" href="/custom-orders" />
          <AttentionTile label="Low stock" sub="Low stock items" count={ops?.low_stock_items ?? 0} icon="inventory_2" href="/inventory" tone="warning" />
          <AttentionTile label="Pending POs" sub="Pending POs" count={ops?.pending_purchase_orders ?? 0} icon="local_mall" href="/purchase-orders" tone="warning" />
          <AttentionTile label="Open tills" sub="Open tills" count={ops?.open_tills ?? 0} icon="point_of_sale" href="/pos-reports" />
          <AttentionTile label="Active couriers" sub="Active couriers" count={ops?.active_couriers ?? 0} icon="two_wheeler" href="/delivery-zones" />
        </div>
      </Section>

      {/* Today's mix — where the orders and money came from */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-8">
        <div className="bg-white border border-gray-200 p-4">
          <h3 className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-4">By Channel</h3>
          <BreakdownBars rows={data?.by_channel ?? []} empty="No orders yet today" />
        </div>
        <div className="bg-white border border-gray-200 p-4">
          <h3 className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-4">By Fulfilment</h3>
          <BreakdownBars rows={data?.by_fulfillment ?? []} empty="No orders yet today" />
        </div>
        <div className="bg-white border border-gray-200 p-4">
          <h3 className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-4">By Payment</h3>
          <BreakdownBars rows={data?.by_payment ?? []} empty="No orders yet today" />
        </div>
      </div>

      {/* Orders by status — click a status to narrow every figure above to it */}
      {data && data.by_status.length > 0 && (
        <Section title="Orders by Status">
          <div className="bg-white border border-gray-200 p-4 flex flex-wrap gap-2">
            {data.by_status.map((row) => {
              const key = statusKey(row.label);
              const on = statuses.includes(key);
              return (
                <button
                  key={row.label}
                  onClick={() => toggleStatus(key)}
                  aria-pressed={on}
                  title={on ? 'Remove from filter' : 'Filter to this status'}
                  className={cn(
                    'flex items-center gap-2 border px-3 py-1.5 transition-colors',
                    on ? 'border-primary bg-primary/5' : 'border-gray-100 hover:border-gray-300',
                  )}
                >
                  <Badge variant={STATUS_BADGE[key] ?? 'neutral'}>{row.label}</Badge>
                  <span className="font-display text-sm text-gray-800">{row.orders}</span>
                </button>
              );
            })}
          </div>
        </Section>
      )}

      {/* Quick Actions */}
      <Section title="Quick Actions">
        <div className="flex flex-wrap gap-2">
          <Link href="/products/new" className="flex items-center gap-1.5 px-4 py-2 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity">
            <span className="material-icons text-[14px]">add</span>
            New Product
          </Link>
          <Link href="/custom-orders" className="flex items-center gap-1.5 px-4 py-2 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors">
            <span className="material-icons text-[14px]">cake</span>
            Custom Orders
          </Link>
          <Link href="/orders" className="flex items-center gap-1.5 px-4 py-2 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors">
            <span className="material-icons text-[14px]">visibility</span>
            View All Orders
          </Link>
          <Link href="/analytics" className="flex items-center gap-1.5 px-4 py-2 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors">
            <span className="material-icons text-[14px]">insights</span>
            Analytics
          </Link>
        </div>
      </Section>

      {/* Recent orders — or the search results when the box has a query */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500">
            {searching ? `Search results for “${debouncedSearch}”` : 'Recent Orders'}
          </h2>
          <Link href="/orders" className="inline-flex items-center min-h-11 md:min-h-0 text-xs text-primary hover:underline font-body">
            View all
          </Link>
        </div>

        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-12 bg-gray-100 animate-pulse" />
            ))}
          </div>
        ) : orders.length === 0 ? (
          <div className="text-center py-10 bg-white border border-gray-200">
            <p className="text-sm text-gray-400 font-body">
              {searching ? 'No orders match that search' : 'No orders yet'}
            </p>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 divide-y divide-gray-100">
            {orders.map((order) => (
              <Link
                key={order.id}
                href={`/orders/${order.order_number}`}
                className="flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center gap-4 min-w-0">
                  <span className="text-xs font-mono text-gray-700 font-medium shrink-0">{order.order_number}</span>
                  <span className="text-xs text-gray-400 font-body truncate hidden sm:block">{order.email}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Badge variant={STATUS_BADGE[order.status] ?? 'neutral'}>{order.status}</Badge>
                  <span className="text-xs font-body text-gray-700">{formatCurrency(order.total)}</span>
                  <span className="text-[11px] text-gray-400 font-body hidden sm:block">{formatTimeAgo(order.created_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
