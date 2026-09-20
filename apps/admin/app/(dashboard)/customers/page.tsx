'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import type { Schemas } from '@mm/types';
import { customersApi } from '@/lib/api';
import { Button, Input, LoadError, Pagination, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { Modal } from '@/components/pos/ResourcePage';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { formatCurrency, formatDate } from '@/lib/utils';

type CustomerSummary = Schemas['CustomerSummary'];
type CustomerOrder = Schemas['CustomerOrderHistoryRow'];
type CustomerOrdersPage = Schemas['PaginatedCustomerOrders'];

export default function CustomersPage() {
  const [search, setSearch] = useState('');
  const [selectedCustomer, setSelectedCustomer] = useState<CustomerSummary | null>(null);
  const [history, setHistory] = useState<CustomerOrdersPage | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPerPage, setHistoryPerPage] = useState(50);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const debouncedSearch = useDebouncedValue(search);

  const fetchCustomers = useCallback(
    (page: number, perPage: number) => customersApi.list({
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

  const loadHistory = useCallback(async (
    customerId: string,
    requestedPage: number,
    requestedPerPage: number,
  ) => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      setHistory(await customersApi.orders(customerId, {
        page: requestedPage,
        per_page: requestedPerPage,
      }));
    } catch (error) {
      setHistoryError((error as Error).message || 'Could not load this customer’s orders.');
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedCustomer) void loadHistory(selectedCustomer.id, historyPage, historyPerPage);
  }, [historyPage, historyPerPage, loadHistory, selectedCustomer]);

  function openOrders(customer: CustomerSummary) {
    setSelectedCustomer(customer);
    setHistory(null);
    setHistoryError('');
    setHistoryPage(1);
    setHistoryPerPage(50);
  }

  function closeOrders() {
    setSelectedCustomer(null);
    setHistory(null);
    setHistoryError('');
  }

  return (
    <div>
      <LoadError message={loadError} onRetry={refetch} />
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Customers</h1>
          <p className="mt-0.5 text-xs font-body text-gray-400">
            {total} people across website, counter, and marketplace orders
          </p>
        </div>
      </div>

      <div className="mb-4 max-w-md">
        <Input
          placeholder="Search name, email, or phone…"
          value={search}
          onChange={event => setSearch(event.target.value)}
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<CustomerSummary>
          rows={customers}
          rowKey={customer => customer.id}
          stickyHeader
          empty={<p className="py-16 text-center text-sm font-body text-gray-400">No customers found.</p>}
          actions={customer => (
            <Button
              variant="ghost"
              size="sm"
              className="w-full sm:w-auto"
              onClick={() => openOrders(customer)}
            >
              View orders
            </Button>
          )}
          columns={[
            {
              header: 'Customer',
              priority: 'primary',
              render: customer => (
                <span className="text-xs font-body font-medium text-gray-800">
                  {customer.name || customer.email || customer.phone || 'Unknown customer'}
                </span>
              ),
            },
            {
              header: 'Email',
              priority: 'secondary',
              render: customer => customer.email ? (
                <span className="break-all text-xs text-gray-600">{customer.email}</span>
              ) : '—',
            },
            {
              header: 'Phone',
              priority: 'secondary',
              render: customer => customer.phone ?? '—',
            },
            { header: 'Orders', className: 'text-center', render: customer => customer.order_count },
            {
              header: 'First order',
              render: customer => customer.earliest_order_at
                ? formatDate(customer.earliest_order_at)
                : '—',
            },
            {
              header: 'Last order',
              render: customer => customer.latest_order_at
                ? formatDate(customer.latest_order_at)
                : '—',
            },
            {
              header: 'Revenue',
              className: 'text-right',
              render: customer => formatCurrency(customer.total_revenue),
            },
            {
              header: 'AOV',
              className: 'text-right',
              render: customer => formatCurrency(customer.aov),
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

      {selectedCustomer && (
        <Modal title={`Order history — ${selectedCustomer.name || selectedCustomer.email || selectedCustomer.phone || 'Customer'}`} onClose={closeOrders} wide>
          {historyError ? (
            <LoadError
              message={historyError}
              onRetry={() => void loadHistory(selectedCustomer.id, historyPage, historyPerPage)}
            />
          ) : historyLoading || !history ? (
            <div className="flex justify-center py-12"><Spinner /></div>
          ) : (
            <>
              <DataTable<CustomerOrder>
                rows={history.items}
                rowKey={order => order.id}
                empty={<p className="py-12 text-center text-sm font-body text-gray-400">No orders found.</p>}
                columns={[
                  {
                    header: 'Order #',
                    priority: 'primary',
                    render: order => (
                      <Link className="font-medium text-primary hover:underline" href={`/orders/${order.order_number}`}>
                        {order.order_number}
                      </Link>
                    ),
                  },
                  {
                    header: 'Customer',
                    priority: 'secondary',
                    render: order => order.customer_name || order.customer_email || order.customer_phone || '—',
                  },
                  { header: 'Phone', render: order => order.customer_phone ?? '—' },
                  { header: 'Email', render: order => order.customer_email ?? '—' },
                  { header: 'Order date', render: order => formatDate(order.order_date) },
                  { header: 'Channel', render: order => order.order_channel },
                  {
                    header: 'Value',
                    className: 'text-right',
                    render: order => formatCurrency(order.order_value),
                  },
                ]}
              />
              <Pagination
                page={history.page}
                pages={history.pages}
                total={history.total}
                perPage={history.per_page}
                onPageChange={setHistoryPage}
                onPerPageChange={setHistoryPerPage}
                label="orders"
              />
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
