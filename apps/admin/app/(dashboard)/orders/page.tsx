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
import { useRouter, useSearchParams } from 'next/navigation';
import { ordersApi, exportApi } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type { Order, OrderStatus } from '@/lib/types';
import { Badge, Button, Input, Pagination, Select, TabBar, LoadError, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { formatCurrency, formatDate } from '@/lib/utils';

const STATUS_OPTIONS = [
  { value: '', label: 'All Statuses' },
  { value: 'created', label: 'Created' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'arrived_at_pos', label: 'At the shop' },
  { value: 'packed', label: 'Packed' },
  { value: 'out_for_delivery', label: 'On The Way' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'undelivered', label: 'Undelivered' },
  { value: 'cancelled', label: 'Cancelled' },
];

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

type Channel = '' | 'online' | 'counter';

export default function OrdersPage() {
  const router = useRouter();
  const params = useSearchParams();

  const [branches, setBranches] = useState<Branch[]>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [branchId, setBranchId] = useState('');
  const [exportError, setExportError] = useState('');
  // Deep-linkable, so the old /pos-orders bookmark can land here on the right tab.
  const [channel, setChannel] = useState<Channel>(
    (params.get('channel') as Channel) || '',
  );
  const debouncedSearch = useDebouncedValue(search);

  useEffect(() => {
    void branchesApi
      .list()
      .then(rows => setBranches(rows.filter(b => !b.deleted_at)))
      .catch(() => setBranches([]));
  }, []);

  // Server-side pagination: `/orders/admin/all` pages and filters in SQL, and
  // the hook resets to page 1 whenever a filter changes this fetcher.
  const fetchOrders = useCallback(
    (page: number, perPage: number) =>
      ordersApi.listAll({
        search: debouncedSearch || undefined,
        status: statusFilter || undefined,
        channel: channel || undefined,
        branch_id: branchId || undefined,
        page,
        per_page: perPage,
      }),
    [debouncedSearch, statusFilter, channel, branchId],
  );

  const {
    items: orders, total, pages, page, perPage, setPage, setPerPage,
    loading, loadError, refetch,
  } = useApiList<Order>({ paginate: 'server', fetch: fetchOrders });

  async function exportCsv() {
    setExportError('');
    try {
      await exportApi.exportOrders({ status: statusFilter || undefined });
    } catch (err) {
      setExportError((err as Error).message);
    }
  }

  const showCounterColumns = channel !== 'online';
  const showOnlineColumns = channel !== 'counter';
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

      <TabBar
        tabs={[
          { key: '', label: 'All' },
          { key: 'online', label: 'Website' },
          { key: 'counter', label: 'Counter' },
        ]}
        active={channel}
        onChange={key => setChannel(key as Channel)}
      />

      <div className="flex flex-wrap gap-3 mb-4">
        <div className="flex-1 min-w-[16rem] max-w-xs">
          <Input
            placeholder="Search order #, email, name or phone…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="w-44">
          <Select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            options={STATUS_OPTIONS}
          />
        </div>
        <div className="w-52">
          <Select
            value={branchId}
            onChange={e => setBranchId(e.target.value)}
            options={branches.map(b => ({
              value: b.id,
              label: `${b.reference} · ${b.name}`,
            }))}
            placeholder="All branches"
          />
        </div>
      </div>

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
              render: o => o.customer_name || o.email,
            },
            ...(showCounterColumns
              ? [
                  {
                    header: 'Check',
                    className: 'text-center',
                    render: (o: Order) => o.check_number ?? '—',
                  },
                ]
              : []),
            ...(channel === ''
              ? [
                  {
                    header: 'Channel',
                    className: 'text-center',
                    render: (o: Order) => (
                      <Badge variant={o.source === 'cashier' ? 'neutral' : 'info'}>
                        {o.source === 'cashier' ? 'Counter' : 'Website'}
                      </Badge>
                    ),
                  },
                ]
              : []),
            {
              header: 'Total',
              className: 'text-right',
              render: o => formatCurrency(o.total),
            },
            {
              header: 'Status',
              className: 'text-center',
              render: o => <Badge variant={STATUS_VARIANT[o.status]}>{o.status}</Badge>,
            },
            ...(showCounterColumns
              ? [
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
                ]
              : []),
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
            ...(showOnlineColumns
              ? [
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
                ]
              : []),
            {
              header: 'Date',
              className: 'text-right',
              render: o => <span className="text-gray-400">{formatDate(o.created_at)}</span>,
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
