'use client';

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { customersApi } from '@/lib/api';
import type { CustomerSummary } from '@/lib/types';
import { Button, Input, Pagination, LoadError, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
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

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<CustomerSummary>
          rows={customers}
          rowKey={c => c.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">No customers found.</p>
          }
          actions={c => (
            <Button
              variant="ghost"
              size="sm"
              className="w-full sm:w-auto"
              onClick={() => router.push(`/orders?search=${encodeURIComponent(c.email)}`)}
            >
              View Orders
            </Button>
          )}
          columns={[
            {
              header: 'Email',
              priority: 'primary',
              render: c => (
                <span className="text-xs font-body font-medium text-gray-800 break-all">
                  {c.email}
                </span>
              ),
            },
            {
              header: 'Phone',
              // A subtitle rather than a labelled line: under an email address
              // a phone number needs no introduction.
              priority: 'secondary',
              render: c => c.phone ?? '—',
            },
            { header: 'Orders', className: 'text-center', render: c => c.order_count },
            {
              header: 'Total Spent',
              className: 'text-right',
              render: c => formatCurrency(c.total_spent),
            },
            {
              header: 'Joined',
              className: 'text-right',
              render: c => <span className="text-gray-400">{formatDate(c.created_at)}</span>,
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
        label="customers"
      />
    </div>
  );
}
