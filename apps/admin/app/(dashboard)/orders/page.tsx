'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { ordersApi } from '@/lib/api';
import type { Order, OrderStatus } from '@/lib/types';
import { Badge, Button, Input, Select } from '@/components/ui';
import { formatCurrency, formatDate } from '@/lib/utils';

const STATUS_OPTIONS = [
  { value: '', label: 'All Statuses' },
  { value: 'created', label: 'Created' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'packed', label: 'Packed' },
  { value: 'cancelled', label: 'Cancelled' },
];

const STATUS_VARIANT: Record<OrderStatus, 'warning' | 'info' | 'success' | 'danger'> = {
  created: 'warning',
  confirmed: 'info',
  packed: 'success',
  cancelled: 'danger',
};

export default function OrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState('');

  // Debounce search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 350);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [search]);

  useEffect(() => { setPage(1); }, [debouncedSearch, statusFilter]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await ordersApi.listAll({
        search: debouncedSearch || undefined,
        status: statusFilter || undefined,
        page,
        per_page: 20,
      });
      setOrders(res.items);
      setTotal(res.total);
      setPages(res.pages);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, statusFilter, page]);

  useEffect(() => { load(); }, [load]);

  function exportCsv() {
    const headers = ['Order #', 'Email', 'Items', 'Total', 'Status', 'Payment', 'Date'];
    const rows = orders.map(o => [
      o.order_number,
      o.email,
      String(o.item_count ?? o.items?.length ?? 0),
      String(o.total),
      o.status,
      o.payment_provider ?? '',
      formatDate(o.created_at),
    ]);
    const csv = [headers, ...rows].map(r => r.map(c => `"${c}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `orders-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Orders</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{total} total</p>
        </div>
        <Button variant="ghost" size="sm" onClick={exportCsv} disabled={orders.length === 0}>
          <span className="material-icons text-[14px]">download</span>
          Export CSV
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-4">
        <div className="flex-1 max-w-xs">
          <Input
            placeholder="Search order # or email…"
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
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Order #</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Customer</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Items</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Total</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Status</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Payment</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Date</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 7 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : orders.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
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
                  <td className="px-4 py-3 hidden sm:table-cell">
                    <div className="text-xs font-body text-gray-700">{order.email}</div>
                  </td>
                  <td className="px-4 py-3 text-center hidden md:table-cell">
                    <span className="text-xs font-body text-gray-500">
                      {order.item_count ?? order.items?.length ?? '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-xs font-body text-gray-700">{formatCurrency(order.total)}</span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <Badge variant={STATUS_VARIANT[order.status]}>
                      {order.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-center hidden lg:table-cell">
                    <span className="text-xs font-body text-gray-400 capitalize">
                      {order.payment_provider ?? '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right hidden lg:table-cell">
                    <span className="text-xs font-body text-gray-400">{formatDate(order.created_at)}</span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <p className="text-xs text-gray-400 font-body">
            Page {page} of {pages} · {total} orders
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>
              <span className="material-icons text-[14px]">chevron_left</span>
            </Button>
            <Button variant="ghost" size="sm" disabled={page === pages} onClick={() => setPage(p => p + 1)}>
              <span className="material-icons text-[14px]">chevron_right</span>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
