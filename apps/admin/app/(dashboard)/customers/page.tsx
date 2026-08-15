'use client';

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { customersApi } from '@/lib/api';
import type { CustomerSummary } from '@/lib/types';
import { Button, Input, Pagination, LoadError} from '@/components/ui';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { formatCurrency, formatDate } from '@/lib/utils';

export default function CustomersPage() {
  const router = useRouter();
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search);

  // Server-side pagination: `/users/admin/all` pages and searches in SQL.
  const fetchCustomers = useCallback(
    (page: number, perPage: number) =>
      customersApi.list({
        search: debouncedSearch || undefined,
        page,
        per_page: perPage,
      }),
    [debouncedSearch],
  );

  const {
    items: customers, total, pages, page, perPage, setPage, setPerPage,
    loading, loadError, refetch,
  } = useApiList<CustomerSummary>({ paginate: 'server', fetch: fetchCustomers });

  return (
    <div>
      <LoadError message={loadError} onRetry={refetch} />
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Customers</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{total} registered</p>
        </div>
      </div>

      {/* Search */}
      <div className="mb-4 max-w-xs">
        <Input
          placeholder="Search by email…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Email</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Phone</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Orders</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Total Spent</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Joined</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 6 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : customers.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No customers found.
                </td>
              </tr>
            ) : (
              customers.map(c => (
                <tr key={c.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3">
                    <span className="text-xs font-body font-medium text-gray-800">{c.email}</span>
                  </td>
                  <td className="px-4 py-3 hidden sm:table-cell">
                    <span className="text-xs font-body text-gray-500">{c.phone ?? '—'}</span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="text-xs font-body text-gray-700">{c.order_count}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-xs font-body text-gray-700">{formatCurrency(c.total_spent)}</span>
                  </td>
                  <td className="px-4 py-3 text-right hidden lg:table-cell">
                    <span className="text-xs font-body text-gray-400">{formatDate(c.created_at)}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => router.push(`/orders?search=${encodeURIComponent(c.email)}`)}
                    >
                      View Orders
                    </Button>
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
        label="customers"
      />
    </div>
  );
}
