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
import { ordersApi, exportApi, categoriesApi } from '@/lib/api';
import { branchesApi, legalEntitiesApi } from '@/lib/pos-api';
import type { Branch, LegalEntity } from '@/lib/pos-types';
import type { Category, Order, OrderStatus } from '@/lib/types';
import { Badge, Button, Pagination, LoadError, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { CourierLogo } from '@/components/orders/CourierLogo';
import { OrderFilterBar } from '@/components/orders/OrderFilterBar';
import { OrderPnlDialog } from '@/components/orders/PnlBreakdown';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useOrderFilters, toOrdersParams } from '@/lib/order-filters';
import { cn, formatCurrency, formatDate, formatTime } from '@/lib/utils';
import { shownOrderNumber } from '@/lib/order-display';

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
 * What the order kept, as PC3 over GMV — both net of VAT, from the API.
 *
 * A button, not text: the cell opens the working behind the number (the
 * order's GMV → PC3 breakdown). It sits inside the row's link, so the click is
 * stopped before it navigates. A dash is an order not in the P&L — in flight,
 * or cancelled with nothing charged. A charged cancellation has no GMV, so it
 * shows what it cost instead of a percentage. Amber means a figure is still to
 * land (no COGS recorded, or a commission awaiting its statement), so the
 * number will move.
 */
function ProfitCell({ order, onOpen }: { order: Order; onOpen: () => void }) {
  const pnl = order.pnl;
  if (!pnl) {
    return (
      <span className="text-gray-300" title="Not in the P&L — still in flight, or cancelled without a charge.">
        —
      </span>
    );
  }
  // A charged cancellation sent no goods, so a missing COGS is not a gap there.
  const cogsGap = pnl.is_sale && pnl.cogs_missing;
  const incomplete = cogsGap || pnl.fees_pending;
  const label =
    pnl.pc3_pct === null ? formatCurrency(pnl.pc3) : `${pnl.pc3_pct.toFixed(0)}%`;
  return (
    <button
      type="button"
      onClick={e => {
        e.preventDefault();
        e.stopPropagation();
        onOpen();
      }}
      className={cn(
        'whitespace-nowrap font-body text-xs tabular-nums underline decoration-dotted underline-offset-2',
        pnl.pc3 < 0 ? 'text-red-600' : 'text-green-700',
        incomplete && 'decoration-amber-500',
      )}
      title={`PC3 ${formatCurrency(pnl.pc3)} of ${formatCurrency(pnl.gmv)} GMV, net of VAT${
        cogsGap ? ' · no COGS recorded' : ''
      }${pnl.fees_pending ? ' · a fee is still to land' : ''}`}
    >
      {label}
      {incomplete && <span className="text-amber-600">*</span>}
    </button>
  );
}

export default function OrdersPage() {
  const {
    filters,
    patch,
    toggleStatus,
    toggleCourier,
    toggleBranch,
    toggleLegalEntity,
    toggleCategory,
    clearAll,
  } = useOrderFilters();

  const [branches, setBranches] = useState<Branch[]>([]);
  const [legalEntities, setLegalEntities] = useState<LegalEntity[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [exportError, setExportError] = useState('');
  // The order whose P&L breakdown is open, if any.
  const [pnlOrder, setPnlOrder] = useState<string | null>(null);
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
    void legalEntitiesApi
      .list()
      .then(setLegalEntities)
      .catch(() => setLegalEntities([]));
    void categoriesApi
      .list()
      .then(rows => setCategories(rows.filter(c => c.is_active)))
      .catch(() => setCategories([]));
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
        onToggleBranch={toggleBranch}
        onToggleLegalEntity={toggleLegalEntity}
        onToggleCategory={toggleCategory}
        onClearAll={() => {
          setSearchInput('');
          clearAll();
        }}
        branchOptions={branches.map(b => ({
          value: b.id,
          label: `${b.reference} · ${b.name}`,
        }))}
        legalEntityOptions={legalEntities.map(e => ({
          value: e.id,
          label: e.brand_name,
        }))}
        categoryOptions={categories.map(c => ({
          value: c.id,
          label: c.name,
        }))}
      />

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<Order>
          rows={orders}
          rowKey={o => o.id}
          stickyHeader
          // Every channel's row opens the one order page — a custom order's
          // pack, courier, recipe and invoice controls live there too.
          getRowHref={o => `/orders/${encodeURIComponent(o.order_number)}`}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">No orders found.</p>
          }
          columns={[
            {
              header: 'Order #',
              // What the shop says on the phone to a customer, so it is what
              // identifies the row in both shapes.
              priority: 'primary',
              // A local-first counter sale shows the ticket the customer holds
              // (`T1-0042`), with the server's number beneath it.
              render: o => (
                <div className="leading-tight">
                  <span className="font-body font-medium text-primary text-xs">
                    {shownOrderNumber(o)}
                  </span>
                  {shownOrderNumber(o) !== o.order_number && (
                    <div className="text-[11px] text-gray-400">{o.order_number}</div>
                  )}
                </div>
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
              // identifies it at a glance — in place of a bare badge; counter,
              // website delivery and store pickup keep their word. A website
              // pickup is its own channel (`online` + `delivery_method` pickup).
              // A custom order is its own channel whoever carried it, so it keeps
              // its word too, with the courier's mark beside it when there is one.
              render: (o: Order) =>
                o.source === 'custom' ? (
                  <span className="inline-flex items-center justify-center gap-1.5">
                    <Badge variant="neutral">Custom order</Badge>
                    {o.courier && <CourierLogo courier={o.courier} size={18} />}
                  </span>
                ) : o.courier ? (
                  <span className="inline-flex justify-center">
                    <CourierLogo courier={o.courier} size={22} showName />
                  </span>
                ) : o.source === 'cashier' ? (
                  <Badge variant="neutral">Counter</Badge>
                ) : o.delivery_method === 'pickup' ? (
                  <Badge variant="neutral">Store Pickup</Badge>
                ) : (
                  <Badge variant="info">Website Delivery</Badge>
                ),
            },
            {
              header: 'Total',
              className: 'text-right',
              render: o => formatCurrency(o.total),
            },
            {
              header: 'Profit',
              className: 'text-center',
              render: o => <ProfitCell order={o} onOpen={() => setPnlOrder(o.order_number)} />,
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

      {pnlOrder && <OrderPnlDialog orderNumber={pnlOrder} onClose={() => setPnlOrder(null)} />}

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
