'use client';

/**
 * One orders screen, both channels.
 *
 * There used to be two — "Orders" for the storefront and "POS Orders" for the
 * counter — plus a third partial view on the dashboard. They were always one
 * ledger underneath: web and POS orders have shared the `orders` table from the
 * start. Two screens meant answering "how many orders today" twice and adding
 * them up, and it meant a website order that now lands on a register showed up
 * in both.
 *
 * So: one list, a channel filter, and columns that change with it. The counter
 * cares about the check number and which kitchen; the storefront cares about
 * the customer and what they paid. Showing both sets at once would be a table
 * half full of dashes.
 */

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { ordersApi, exportApi } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type { Order, OrderStatus } from '@/lib/types';
import { Badge, Button, Pagination, LoadError, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { CourierLogo } from '@/components/orders/CourierLogo';
import { OrderFilterBar } from '@/components/orders/OrderFilterBar';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useOrderFilters, toOrdersParams } from '@/lib/order-filters';
import { cn, formatCurrency, formatDate, formatTime } from '@/lib/utils';

// Packed is no longer the end of the line, so it reads as in-progress and only
// a delivered order gets the green.
const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  arrived_at_pos: 'info',
  packed: 'info',
  out_for_delivery: 'info',
  delivered: 'success',
  // Red, not amber: a paid order sitting undelivered is somebody's afternoon,
  // and it should be as loud on the list as a cancellation.
  undelivered: 'danger',
  cancelled: 'danger',
  payment_failed: 'danger',
  // Amber, not red: the money is settled and nothing is owed: it is a closed
  // order, not one that needs somebody's afternoon.
  refunded: 'warning',
  disputed: 'danger',
};

// The counter lifecycle, which is a different shape from `status` and is shown
// beside it rather than instead of it.
const POS_STATUS_VARIANT: Record<string, 'warning' | 'info' | 'success' | 'danger' | 'neutral'> = {
  pending: 'warning',
  active: 'info',
  closed: 'success',
  draft: 'neutral',
  declined: 'danger',
  void: 'danger',
  returned: 'warning',
  joined: 'neutral',
};

/**
 * Whether an order kept enough of its own menu value to be worth taking.
 *
 * The question a list of orders is actually being scanned for, and the one the
 * screen could not answer: the total says what came in, and nothing said what
 * survived the commission, the van and the card fee. A shop losing money does
 * not lose it evenly — it loses it on particular channels, particular zones and
 * particular discounts — and this column is where that becomes visible without
 * opening thirty orders.
 *
 * **Three states, and the third is the important one.** A dash means *we cannot
 * say*: an aggregator whose commission rate nobody has configured has an
 * unknowable net, and colouring it red would file it beside the orders that are
 * genuinely underwater. That is how an unfilled rate turns into a fortnight of
 * investigating the wrong orders, so the unknown gets its own, quiet, treatment
 * and a tooltip that says what to do about it.
 */
function CostCover({ order }: { order: Order }) {
  const covered = order.covers_direct_cost;
  const share = order.cost_cover;

  if (covered === null || covered === undefined || share === null || share === undefined) {
    return (
      <span
        className="text-gray-300"
        title={
          order.source === 'aggregator'
            ? 'No commission rate is set for this marketplace, so what the order kept cannot be worked out. Set one under Delivery → Estimates.'
            : 'Not enough is known about this order to say.'
        }
      >
        —
      </span>
    );
  }

  return (
    <span
      className={cn(
        'font-body text-xs tabular-nums',
        covered ? 'text-green-700' : 'text-red-600',
      )}
      title={`Kept ${formatCurrency(order.net_value ?? 0)} of ${formatCurrency(
        // The denominator: menu price before discount. Shown in the tooltip so
        // a surprising percentage can be checked rather than argued with.
        order.net_value != null && share !== 0 ? (order.net_value / share) * 100 : 0,
      )} at menu price`}
    >
      {covered ? '✓' : '✕'} {share.toFixed(0)}%
    </span>
  );
}

export default function OrdersPage() {
  const router = useRouter();
  const { filters, patch, toggleStatus, toggleCourier, clearAll } = useOrderFilters();

  const [branches, setBranches] = useState<Branch[]>([]);
  const [exportError, setExportError] = useState('');
  // The search box is responsive while typing; the committed value lives in the
  // URL (so it persists and is shareable), written after a short debounce.
  const [searchInput, setSearchInput] = useState(filters.search);
  const debouncedSearch = useDebouncedValue(searchInput);

  useEffect(() => {
    if (debouncedSearch !== filters.search) patch({ search: debouncedSearch });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  useEffect(() => {
    void branchesApi
      .list()
      .then(rows => setBranches(rows.filter(b => !b.deleted_at)))
      .catch(() => setBranches([]));
  }, []);

  const orderParams = toOrdersParams(filters);

  // Server-side pagination: `/orders/admin/all` pages and filters in SQL, and
  // the hook resets to page 1 whenever a filter changes this fetcher.
  const fetchOrders = useCallback(
    (page: number, perPage: number) =>
      ordersApi.listAll({ ...orderParams, page, per_page: perPage }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(orderParams)],
  );

  const {
    items: orders, total, pages, page, perPage, setPage, setPerPage,
    loading, loadError, refetch,
  } = useApiList<Order>({ paginate: 'server', fetch: fetchOrders });

  async function exportCsv() {
    setExportError('');
    try {
      await exportApi.exportOrders({ status: filters.statuses[0] || undefined });
    } catch (err) {
      setExportError((err as Error).message);
    }
  }

  const branchRef = (id: string | null | undefined) =>
    branches.find(b => b.id === id)?.reference ?? '—';

  return (
    <div>
      <LoadError message={loadError || exportError} onRetry={refetch} />
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Orders</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            {total} total · the storefront and the counter, one ledger
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={exportCsv} disabled={orders.length === 0}>
          <span className="material-icons text-[14px]">download</span>
          Export CSV
        </Button>
      </div>

      <OrderFilterBar
        filters={filters}
        search={searchInput}
        onSearch={setSearchInput}
        onPatch={patch}
        onToggleStatus={toggleStatus}
        onToggleCourier={toggleCourier}
        onClearAll={() => {
          setSearchInput('');
          clearAll();
        }}
        branchOptions={branches.map(b => ({
          value: b.id,
          label: `${b.reference} · ${b.name}`,
        }))}
      />

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<Order>
          rows={orders}
          rowKey={o => o.id}
          onRowClick={o => router.push(`/orders/${o.order_number}`)}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">No orders found.</p>
          }
          columns={[
            {
              header: 'Order #',
              // What the shop says on the phone to a customer, so it is what
              // identifies the row in both shapes.
              priority: 'primary',
              render: o => (
                <span className="font-body font-medium text-primary text-xs">
                  {o.order_number}
                </span>
              ),
            },
            {
              header: 'Customer',
              priority: 'secondary',
              // Name with the number beneath it — the phone shows wherever the
              // name does, and for a Deliveroo order it carries the access code.
              render: o => (
                <div className="leading-tight">
                  <span>{o.customer_name || o.email || '—'}</span>
                  {o.customer_phone && (
                    <span className="block text-xs text-gray-400">{o.customer_phone}</span>
                  )}
                </div>
              ),
            },
            {
              header: 'Check',
              className: 'text-center',
              render: (o: Order) => o.check_number ?? '—',
            },
            {
              header: 'Channel',
              className: 'text-center',
              // An aggregator order shows the marketplace's logo — the thing that
              // identifies it at a glance — in place of a bare badge; counter and
              // website keep their word.
              render: (o: Order) =>
                o.courier ? (
                  <span className="inline-flex justify-center">
                    <CourierLogo courier={o.courier} size={22} showName />
                  </span>
                ) : (
                  <Badge variant={o.source === 'cashier' ? 'neutral' : 'info'}>
                    {o.source === 'cashier' ? 'Counter' : 'Website'}
                  </Badge>
                ),
            },
            {
              header: 'Total',
              className: 'text-right',
              render: o => formatCurrency(o.total),
            },
            {
              header: 'Covers cost',
              className: 'text-center',
              render: o => <CostCover order={o} />,
            },
            {
              header: 'Status',
              className: 'text-center',
              render: o => <Badge variant={STATUS_VARIANT[o.status]}>{o.status}</Badge>,
            },
            {
              header: 'Counter',
              className: 'text-center',
              render: (o: Order) =>
                o.pos_status ? (
                  <Badge variant={POS_STATUS_VARIANT[o.pos_status] ?? 'neutral'}>
                    {o.pos_status}
                  </Badge>
                ) : (
                  <span className="text-gray-300">—</span>
                ),
            },
            {
              header: 'Branch',
              className: 'text-center',
              render: o => <span className="text-gray-400">{branchRef(o.branch_id)}</span>,
            },
            {
              header: 'Items',
              className: 'text-center',
              render: o => o.item_count ?? o.items?.length ?? '—',
            },
            {
              header: 'Delivery',
              className: 'text-right',
              render: (o: Order) =>
                o.delivery_fee != null
                  ? o.delivery_fee > 0
                    ? formatCurrency(o.delivery_fee)
                    : 'Free'
                  : '—',
            },
            {
              header: 'Date',
              className: 'text-right',
              render: o => (
                <div className="text-gray-400">
                  <div>{formatDate(o.created_at)}</div>
                  <div className="text-xs tabular-nums text-gray-300">
                    {formatTime(o.created_at)}
                  </div>
                </div>
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
        label="orders"
      />
    </div>
  );
}
