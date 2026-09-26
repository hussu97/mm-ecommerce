'use client';

/**
 * Custom orders — bespoke cakes the shop takes by phone, DM or at the counter.
 *
 * Each is an ordinary order (`source = 'custom'`) made at the custom-orders
 * branch, so it reaches stock, the P&L and couriers like any other; this screen
 * is where they are taken, followed and finished. Two views of one list:
 *
 * - **List**, tabbed by where the order is (pending → packed → on the way →
 *   delivered, plus cancelled), searchable and filterable by delivery date.
 * - **Calendar**, a month by delivery date — what the kitchen owes on which day.
 *   There is no capacity here: the old per-day limits went with the booking
 *   calendar this replaced.
 *
 * Every filter lives in the URL (`useUrlFilters`), so a view survives a refresh
 * and pastes to a colleague.
 */

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  customOrdersApi,
  type CustomOrderListItem,
  type CustomOrderStatusGroup,
  type CustomOrdersStatus,
} from '@/lib/api';
import { Button, LoadError, Pagination, Spinner, TabBar } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { FilterBar } from '@/components/ui/FilterBar';
import { Page } from '@/components/ui/Page';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useUrlFilters, type FilterFieldSpec } from '@/lib/list-filters';
import { cn, formatCurrency, todayInShopTz } from '@/lib/utils';
import {
  CustomOrderStatusBadge,
  deliveryLabel,
  providerLabel,
  statusChipClass,
} from '@/components/custom-orders/display';
import { SetupNotice } from '@/components/custom-orders/SetupNotice';

type Filters = {
  tab: string;
  q: string;
  from: string;
  to: string;
  view: string;
  month: string;
};

const FIELDS: FilterFieldSpec[] = [
  { key: 'tab', kind: 'single' },
  { key: 'q', kind: 'single' },
  { key: 'from', kind: 'single' },
  { key: 'to', kind: 'single' },
  { key: 'view', kind: 'single' },
  { key: 'month', kind: 'single' },
];

const TABS: { key: CustomOrderStatusGroup; label: string }[] = [
  { key: 'pending', label: 'Pending' },
  { key: 'packed', label: 'Packed' },
  { key: 'on_the_way', label: 'On the way' },
  { key: 'delivered', label: 'Delivered' },
  { key: 'cancelled', label: 'Cancelled' },
];

const DATE_INPUT =
  'px-3 h-10 border border-gray-300 bg-white text-sm font-body outline-none focus:border-primary';

export default function CustomOrdersPage() {
  const { filters, patch } = useUrlFilters<Filters>(FIELDS);
  const tab = (TABS.some(t => t.key === filters.tab) ? filters.tab : 'pending') as CustomOrderStatusGroup;
  const view = filters.view === 'calendar' ? 'calendar' : 'list';

  const [status, setStatus] = useState<CustomOrdersStatus | null>(null);
  useEffect(() => {
    customOrdersApi.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  // Typing is instant; the URL (and so the request) follows after a pause.
  const [searchInput, setSearchInput] = useState(filters.q);
  const debouncedSearch = useDebouncedValue(searchInput);
  useEffect(() => {
    if (debouncedSearch !== filters.q) patch({ q: debouncedSearch });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const hasDateOrSearch = Boolean(filters.q || filters.from || filters.to);

  return (
    <Page>
      <header className="mb-5 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-xl text-primary tracking-wide">Custom Orders</h1>
          <p className="text-xs text-gray-500 font-body mt-1">
            Bespoke cakes, made at {status?.branch_name ?? 'the custom-orders branch'} — priced,
            stocked and delivered like every other order, reported on the day they are delivered.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex border border-gray-300" role="group" aria-label="View">
            {(['list', 'calendar'] as const).map(v => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => patch({ view: v === 'list' ? '' : v })}
                className={cn(
                  'inline-flex items-center gap-1 px-3 min-h-[var(--tap-min)] md:min-h-0 md:py-1.5 text-xs font-body uppercase tracking-wider',
                  view === v ? 'bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50',
                )}
              >
                <span className="material-icons text-[16px]">
                  {v === 'list' ? 'view_list' : 'calendar_month'}
                </span>
                {v === 'list' ? 'List' : 'Calendar'}
              </button>
            ))}
          </div>
          {status?.enabled ? (
            <Link
              href="/custom-orders/new"
              className="inline-flex items-center gap-1.5 px-4 py-2 min-h-[var(--tap-min)] md:min-h-0 bg-primary text-white text-xs font-body font-medium uppercase tracking-wider hover:opacity-90 transition-opacity"
            >
              <span className="material-icons text-[16px]">add</span>
              New custom order
            </Link>
          ) : (
            <Button disabled title="Custom orders are not set up yet">
              <span className="material-icons text-[16px]">add</span>
              New custom order
            </Button>
          )}
        </div>
      </header>

      {status && !status.enabled && <SetupNotice />}

      {view === 'list' ? (
        <>
          <TabBar tabs={TABS} active={tab} onChange={key => patch({ tab: key === 'pending' ? '' : key })} />
          <FilterBar
            hasAny={hasDateOrSearch}
            onClear={() => {
              setSearchInput('');
              patch({ q: '', from: '', to: '' });
            }}
          >
            <div className="flex-1 min-w-[14rem]">
              <label htmlFor="co-search" className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">
                Search
              </label>
              <input
                id="co-search"
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                placeholder="Order number, customer name or phone, title…"
                className={cn(DATE_INPUT, 'w-full')}
              />
            </div>
            <div>
              <label htmlFor="co-from" className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">
                Delivery from
              </label>
              <input
                id="co-from"
                type="date"
                value={filters.from}
                max={filters.to || undefined}
                onChange={e => patch({ from: e.target.value })}
                className={DATE_INPUT}
              />
            </div>
            <div>
              <label htmlFor="co-to" className="block text-[10px] font-body uppercase tracking-widest text-gray-400 mb-1">
                Delivery to
              </label>
              <input
                id="co-to"
                type="date"
                value={filters.to}
                min={filters.from || undefined}
                onChange={e => patch({ to: e.target.value })}
                className={DATE_INPUT}
              />
            </div>
          </FilterBar>
          <OrdersList tab={tab} q={filters.q} from={filters.from} to={filters.to} />
        </>
      ) : (
        <CalendarView month={filters.month} onMonth={m => patch({ month: m })} />
      )}
    </Page>
  );
}

// ─── List ──────────────────────────────────────────────────────────────────────

function OrdersList({
  tab,
  q,
  from,
  to,
}: {
  tab: CustomOrderStatusGroup;
  q: string;
  from: string;
  to: string;
}) {
  const fetchOrders = useCallback(
    (page: number, perPage: number) =>
      customOrdersApi.list({
        status_group: tab,
        q: q || undefined,
        date_from: from || undefined,
        date_to: to || undefined,
        page,
        per_page: perPage,
      }),
    [tab, q, from, to],
  );

  const {
    items, total, pages, page, perPage, setPage, setPerPage, loading, loadError, refetch,
  } = useApiList<CustomOrderListItem>({ paginate: 'server', fetch: fetchOrders });

  return (
    <>
      <LoadError message={loadError} onRetry={refetch} />
      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <DataTable<CustomOrderListItem>
          rows={items}
          rowKey={o => o.id}
          stickyHeader
          getRowHref={o => `/custom-orders/${encodeURIComponent(o.order_number)}`}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              No custom orders here.
            </p>
          }
          columns={[
            {
              header: 'Order #',
              priority: 'primary',
              render: o => (
                <span className="font-body font-medium text-primary text-xs">{o.order_number}</span>
              ),
            },
            {
              header: 'Delivery',
              priority: 'secondary',
              render: o => (
                <span className="whitespace-nowrap">{deliveryLabel(o.delivery_date, o.delivery_time)}</span>
              ),
            },
            {
              header: 'Customer',
              render: o => o.customer_name || <span className="text-gray-300">—</span>,
            },
            {
              header: 'What',
              render: o => <span className="text-gray-600 line-clamp-2 max-w-sm">{o.summary}</span>,
            },
            {
              header: 'Total',
              className: 'text-right',
              render: o => <span className="tabular-nums">{formatCurrency(o.total)}</span>,
            },
            {
              header: 'Status',
              className: 'text-center',
              render: o => <CustomOrderStatusBadge status={o.status} />,
            },
            {
              header: 'Courier',
              render: o =>
                o.delivery_provider ? (
                  providerLabel(o.delivery_provider)
                ) : (
                  <span className="text-gray-300">—</span>
                ),
            },
            {
              header: 'Docket',
              render: o =>
                o.kitchen_printed_at ? (
                  <span className="text-gray-400">Printed</span>
                ) : o.status === 'cancelled' ? (
                  <span className="text-gray-300">—</span>
                ) : (
                  <span className="text-amber-700">Not printed yet</span>
                ),
            },
          ]}
        />
      )}
      {!loading && (
        <Pagination
          page={page}
          pages={pages}
          total={total}
          perPage={perPage}
          onPageChange={setPage}
          onPerPageChange={setPerPage}
          label="custom orders"
        />
      )}
    </>
  );
}

// ─── Calendar ──────────────────────────────────────────────────────────────────

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
/** The API's page-size ceiling — a month holds far fewer custom orders. */
const MONTH_LIMIT = 2000;

function monthOf(value: string): { y: number; m: number } {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (match) return { y: Number(match[1]), m: Number(match[2]) };
  const [y, m] = todayInShopTz().split('-').map(Number);
  return { y, m };
}

const pad = (n: number) => String(n).padStart(2, '0');

function CalendarView({ month, onMonth }: { month: string; onMonth: (m: string) => void }) {
  const { y, m } = monthOf(month);
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const first = `${y}-${pad(m)}-01`;
  const last = `${y}-${pad(m)}-${pad(daysInMonth)}`;
  // Monday-first grid: how many blank cells before the 1st.
  const lead = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7;
  const today = todayInShopTz();

  const [orders, setOrders] = useState<CustomOrderListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError('');
    customOrdersApi
      .list({ date_from: first, date_to: last, page: 1, per_page: MONTH_LIMIT })
      .then(res => {
        if (!live) return;
        setOrders(res.items);
        setTotal(res.total);
      })
      .catch(err => live && setError((err as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [first, last, tick]);

  const byDay = useMemo(() => {
    const out = new Map<string, CustomOrderListItem[]>();
    for (const o of orders) {
      if (!o.delivery_date) continue;
      const list = out.get(o.delivery_date) ?? [];
      list.push(o);
      out.set(o.delivery_date, list);
    }
    for (const list of out.values()) {
      list.sort((a, b) => (a.delivery_time ?? '99').localeCompare(b.delivery_time ?? '99'));
    }
    return out;
  }, [orders]);

  const shift = (delta: number) => {
    const d = new Date(Date.UTC(y, m - 1 + delta, 1));
    onMonth(`${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}`);
  };
  const title = new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-AE', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  });

  const cells: (number | null)[] = [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
  while (cells.length % 7) cells.push(null);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => shift(-1)} aria-label="Previous month">
            <span className="material-icons text-[16px]">chevron_left</span>
          </Button>
          <h2 className="font-display text-lg text-gray-800 min-w-[10rem] text-center">{title}</h2>
          <Button variant="ghost" size="sm" onClick={() => shift(1)} aria-label="Next month">
            <span className="material-icons text-[16px]">chevron_right</span>
          </Button>
          <Button variant="secondary" size="sm" onClick={() => onMonth('')}>
            This month
          </Button>
        </div>
        <p className="text-xs font-body text-gray-400">
          By delivery date · {total} {total === 1 ? 'order' : 'orders'}
          {total > orders.length && ` (showing the first ${orders.length})`}
        </p>
      </div>

      <LoadError message={error} onRetry={() => setTick(t => t + 1)} />

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <>
          {/* Desktop: a real month grid. */}
          <div className="hidden md:grid grid-cols-7 border-l border-t border-gray-200">
            {WEEKDAYS.map(d => (
              <div
                key={d}
                className="border-r border-b border-gray-200 bg-gray-50 px-2 py-1 text-[10px] font-body uppercase tracking-widest text-gray-400"
              >
                {d}
              </div>
            ))}
            {cells.map((day, i) => {
              const iso = day ? `${y}-${pad(m)}-${pad(day)}` : '';
              const list = day ? byDay.get(iso) ?? [] : [];
              return (
                <div
                  key={i}
                  className={cn(
                    'min-h-28 border-r border-b border-gray-200 p-1.5',
                    !day && 'bg-gray-50/60',
                    iso === today && 'bg-primary/5',
                  )}
                >
                  {day && (
                    <p className={cn('mb-1 text-xs font-body', iso === today ? 'font-semibold text-primary' : 'text-gray-500')}>
                      {day}
                    </p>
                  )}
                  <div className="space-y-1">
                    {list.map(o => (
                      <OrderChip key={o.id} order={o} />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Phone: seven columns do not fit, so it is an agenda of the days that have orders. */}
          <div className="md:hidden space-y-3">
            {[...byDay.keys()].sort().map(iso => (
              <div key={iso}>
                <p className={cn('mb-1 text-xs font-body uppercase tracking-wider', iso === today ? 'text-primary' : 'text-gray-500')}>
                  {deliveryLabel(iso, null)}
                </p>
                <div className="space-y-1">
                  {byDay.get(iso)!.map(o => (
                    <OrderChip key={o.id} order={o} />
                  ))}
                </div>
              </div>
            ))}
            {byDay.size === 0 && (
              <p className="py-12 text-center text-sm text-gray-400 font-body">No custom orders this month.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function OrderChip({ order }: { order: CustomOrderListItem }) {
  return (
    <Link
      href={`/custom-orders/${encodeURIComponent(order.order_number)}`}
      title={`${order.order_number} · ${order.summary}${order.customer_name ? ` · ${order.customer_name}` : ''}`}
      className={cn(
        'block border px-1.5 py-1 text-[11px] font-body leading-tight hover:opacity-80',
        statusChipClass(order.status),
      )}
    >
      <span className="block truncate font-medium">
        {order.delivery_time ? `${order.delivery_time.slice(0, 5)} · ` : ''}
        {order.customer_name || order.order_number}
      </span>
      <span className="block truncate opacity-80">{order.summary}</span>
    </Link>
  );
}
