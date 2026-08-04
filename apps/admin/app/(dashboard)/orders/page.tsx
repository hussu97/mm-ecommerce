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

import { useEffect, useState, useCallback, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ordersApi, exportApi } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type { Order, OrderStatus } from '@/lib/types';
import { Badge, Button, Input, Pagination, Select, TabBar, LoadError} from '@/components/ui';
import { formatCurrency, formatDate } from '@/lib/utils';

const STATUS_OPTIONS = [
  { value: '', label: 'All Statuses' },
  { value: 'created', label: 'Created' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'packed', label: 'Packed' },
  { value: 'out_for_delivery', label: 'On The Way' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'cancelled', label: 'Cancelled' },
];

// Packed is no longer the end of the line, so it reads as in-progress and only
// a delivered order gets the green.
const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  packed: 'info',
  out_for_delivery: 'info',
  delivered: 'success',
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

  const [orders, setOrders] = useState<Order[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [branchId, setBranchId] = useState('');
  // Deep-linkable, so the old /pos-orders bookmark can land here on the right tab.
  const [channel, setChannel] = useState<Channel>(
    (params.get('channel') as Channel) || '',
  );
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState('');

  useEffect(() => {
    void branchesApi
      .list()
      .then(rows => setBranches(rows.filter(b => !b.deleted_at)))
      .catch(() => setBranches([]));
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 350);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, statusFilter, channel, branchId]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const res = await ordersApi.listAll({
        search: debouncedSearch || undefined,
        status: statusFilter || undefined,
        channel: channel || undefined,
        branch_id: branchId || undefined,
        page,
        per_page: perPage,
      });
      setOrders(res.items);
      setTotal(res.total);
      setPages(res.pages);
    } catch (err) {
      setLoadError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, statusFilter, channel, branchId, page, perPage]);

  useEffect(() => {
    void load();
  }, [load]);

  async function exportCsv() {
    try {
      await exportApi.exportOrders({ status: statusFilter || undefined });
    } catch (err) {
      setLoadError((err as Error).message);
    }
  }

  const showCounterColumns = channel !== 'online';
  const showOnlineColumns = channel !== 'counter';
  const branchRef = (id: string | null | undefined) =>
    branches.find(b => b.id === id)?.reference ?? '—';

  return (
    <div>
      <LoadError message={loadError} onRetry={load} />
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

      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Order #</th>
              {showCounterColumns && (
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Check</th>
              )}
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Customer</th>
              {channel === '' && (
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Channel</th>
              )}
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Branch</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Items</th>
              {showOnlineColumns && (
                <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Delivery</th>
              )}
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Total</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Status</th>
              {showCounterColumns && (
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Counter</th>
              )}
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Date</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 9 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : orders.length === 0 ? (
              <tr>
                <td colSpan={11} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No orders found.
                </td>
              </tr>
            ) : (
              orders.map(order => (
                <tr
                  key={order.id}
                  className="hover:bg-gray-50 transition-colors cursor-pointer"
                  onClick={() => router.push(`/orders/${order.order_number}`)}
                >
                  <td className="px-4 py-3 font-body font-medium text-primary text-xs">
                    {order.order_number}
                  </td>
                  {showCounterColumns && (
                    <td className="px-4 py-3 text-center text-xs font-body text-gray-500">
                      {order.check_number ?? '—'}
                    </td>
                  )}
                  <td className="px-4 py-3 hidden sm:table-cell">
                    <div className="text-xs font-body text-gray-700">
                      {order.customer_name || order.email}
                    </div>
                  </td>
                  {channel === '' && (
                    <td className="px-4 py-3 text-center">
                      <Badge variant={order.source === 'cashier' ? 'neutral' : 'info'}>
                        {order.source === 'cashier' ? 'Counter' : 'Website'}
                      </Badge>
                    </td>
                  )}
                  <td className="px-4 py-3 text-center hidden lg:table-cell">
                    <span className="text-xs font-body text-gray-400">
                      {branchRef(order.branch_id)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center hidden md:table-cell">
                    <span className="text-xs font-body text-gray-500">
                      {order.item_count ?? order.items?.length ?? '—'}
                    </span>
                  </td>
                  {showOnlineColumns && (
                    <td className="px-4 py-3 text-right hidden lg:table-cell">
                      <span className="text-xs font-body text-gray-400">
                        {order.delivery_fee != null
                          ? order.delivery_fee > 0
                            ? formatCurrency(order.delivery_fee)
                            : 'Free'
                          : '—'}
                      </span>
                    </td>
                  )}
                  <td className="px-4 py-3 text-right">
                    <span className="text-xs font-body text-gray-700">
                      {formatCurrency(order.total)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <Badge variant={STATUS_VARIANT[order.status]}>{order.status}</Badge>
                  </td>
                  {showCounterColumns && (
                    <td className="px-4 py-3 text-center">
                      {order.pos_status ? (
                        <Badge variant={POS_STATUS_VARIANT[order.pos_status] ?? 'neutral'}>
                          {order.pos_status}
                        </Badge>
                      ) : (
                        <span className="text-xs font-body text-gray-300">—</span>
                      )}
                    </td>
                  )}
                  <td className="px-4 py-3 text-right hidden lg:table-cell">
                    <span className="text-xs font-body text-gray-400">
                      {formatDate(order.created_at)}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

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
