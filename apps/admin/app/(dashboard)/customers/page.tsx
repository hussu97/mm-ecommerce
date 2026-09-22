'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import type { Schemas } from '@mm/types';
import { customersApi } from '@/lib/api';
import { Button, Input, LoadError, Pagination, Spinner } from '@/components/ui';
import { DataTable, type SortState } from '@/components/ui/DataTable';
import { Modal } from '@/components/pos/ResourcePage';
import { CourierLogo, CourierMark } from '@/components/orders/CourierLogo';
import { DateRangePresets } from '@/components/orders/DateRangePresets';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useOrderFilters } from '@/lib/order-filters';
import { formatCurrency, formatDate } from '@/lib/utils';
import { DeliveryAreasMap } from '@/components/customers/DeliveryAreasMap';

type CustomerSummary = Schemas['CustomerSummary'];
type CustomerOrder = Schemas['CustomerOrderHistoryRow'];
type CustomerOrdersPage = Schemas['PaginatedCustomerOrders'];
type CustomerDeliveryAreas = Schemas['CustomerDeliveryAreas'];
type CustomerTab = 'directory' | 'delivery-areas';

function DeliveryAreasPanel({
  search,
  dateFrom,
  dateTo,
}: {
  search: string;
  dateFrom?: string;
  dateTo?: string;
}) {
  const [areas, setAreas] = useState<CustomerDeliveryAreas | null>(null);
  const [metric, setMetric] = useState<'customer_count' | 'revenue' | 'aov'>('customer_count');
  const [showChannelBreakdown, setShowChannelBreakdown] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setAreas(await customersApi.deliveryAreas({
        search: search || undefined,
        date_from: dateFrom,
        date_to: dateTo,
      }));
    } catch (loadError) {
      setError((loadError as Error).message || 'Could not load delivery areas.');
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, search]);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <div className="flex justify-center py-20"><Spinner /></div>;
  if (error) return <LoadError message={error} onRetry={() => void load()} />;
  if (!areas) return null;

  const metricLabel = metric === 'customer_count' ? 'Customers' : metric === 'revenue' ? 'Revenue' : 'AOV';
  const metricValue = metric === 'customer_count'
    ? `${areas.customer_count}`
    : metric === 'revenue'
      ? formatCurrency(areas.revenue)
      : formatCurrency(areas.aov);
  const sourceLabel = Object.entries(areas.source_counts)
    .map(([source, count]) => `${count} ${source}`)
    .join(' · ');

  return (
    <section aria-label="Delivery area demand">
      <div className="mb-4 flex flex-col gap-4 border-y border-gray-200 py-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex divide-x divide-gray-200">
          <div className="pr-5">
            <p className="text-[10px] font-body uppercase tracking-widest text-gray-400">Metric</p>
            <p className="mt-1 text-xl font-display text-gray-800">{metricValue}</p>
            <p className="text-xs font-body text-gray-500">{metricLabel.toLowerCase()} in plotted areas</p>
          </div>
          <div className="px-5">
            <p className="text-[10px] font-body uppercase tracking-widest text-gray-400">Orders</p>
            <p className="mt-1 text-xl font-display text-gray-800">{areas.order_count}</p>
            <p className="text-xs font-body text-gray-500">geocoded delivery orders</p>
          </div>
          <div className="pl-5">
            <p className="text-[10px] font-body uppercase tracking-widest text-gray-400">Live map</p>
            <p className="mt-1 text-sm font-body text-gray-800">{areas.version_name ?? 'No published map'}</p>
            <p className="text-xs font-body text-gray-500">{areas.zones.length} delivery zones</p>
          </div>
        </div>
        <div className="flex flex-col items-start gap-2 lg:items-end">
          <p className="mb-1.5 text-[10px] font-body uppercase tracking-widest text-gray-400">Heat by</p>
          <div className="flex border border-gray-300 bg-white p-0.5">
            {[
              ['customer_count', 'Customers'],
              ['revenue', 'Revenue'],
              ['aov', 'AOV'],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setMetric(value as typeof metric)}
                className={`px-3 py-1.5 text-[11px] font-body transition-colors ${metric === value ? 'bg-gray-800 text-white' : 'text-gray-500 hover:bg-gray-50'}`}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-xs font-body text-gray-600">
            <input
              type="checkbox"
              checked={showChannelBreakdown}
              onChange={event => setShowChannelBreakdown(event.target.checked)}
              className="h-3.5 w-3.5 accent-gray-800"
            />
            Show channel breakdown on zone hover
          </label>
        </div>
      </div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-xs font-body text-gray-500">Warm areas have more {metric === 'customer_count' ? 'customers' : metric === 'revenue' ? 'revenue' : 'higher order values'}; cool areas have less.</p>
        <div className="flex items-center gap-1.5 text-[10px] font-body uppercase tracking-widest text-gray-400"><span className="h-2 w-12 bg-gradient-to-r from-blue-600 via-amber-400 to-rose-600" />Cold · Warm</div>
      </div>
      <DeliveryAreasMap
        data={areas}
        metric={metric}
        showChannelBreakdown={showChannelBreakdown}
      />
      <p className="mt-2 text-[11px] font-body text-gray-400">Each glow combines delivery orders within an approximately 2 km area; no individual home pins are shown.{sourceLabel ? ` Sources: ${sourceLabel}.` : ''}</p>
    </section>
  );
}

export default function CustomersPage() {
  const [search, setSearch] = useState('');
  const [activeTab, setActiveTab] = useState<CustomerTab>('directory');
  const [sort, setSort] = useState<SortState>({ key: 'latest_order_at', direction: 'desc' });
  const [selectedCustomer, setSelectedCustomer] = useState<CustomerSummary | null>(null);
  const [history, setHistory] = useState<CustomerOrdersPage | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPerPage, setHistoryPerPage] = useState(50);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const debouncedSearch = useDebouncedValue(search);
  const { filters, patch } = useOrderFilters();
  const hasCompleteDateRange = Boolean(filters.from && filters.to);

  const fetchCustomers = useCallback(
    (page: number, perPage: number) => customersApi.list({
      search: debouncedSearch || undefined,
      date_from: hasCompleteDateRange ? filters.from : undefined,
      date_to: hasCompleteDateRange ? filters.to : undefined,
      sort_by: sort.key as 'order_count' | 'earliest_order_at' | 'latest_order_at' | 'total_revenue' | 'aov',
      sort_direction: sort.direction,
      page,
      per_page: perPage,
    }),
    [debouncedSearch, filters.from, filters.to, hasCompleteDateRange, sort],
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
      {activeTab === 'directory' && <LoadError message={loadError} onRetry={refetch} />}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Customers</h1>
          <p className="mt-0.5 text-xs font-body text-gray-400">
            {total} people across website, counter, and marketplace orders
          </p>
        </div>
      </div>

      <div className="mb-4 max-w-3xl">
        <div className="mb-3">
          <span className="mb-1.5 block text-[10px] font-body uppercase tracking-widest text-gray-400">Quick range</span>
          <DateRangePresets filters={filters} onPatch={patch} />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-[10px] font-body uppercase tracking-widest text-gray-400">From</label>
            <input
              type="date"
              value={filters.from}
              max={filters.to || undefined}
              onChange={event => patch({ from: event.target.value })}
              className="h-10 border border-gray-300 bg-white px-3 text-sm font-body outline-none focus:border-primary"
            />
          </div>
          <div>
            <label className="mb-1 block text-[10px] font-body uppercase tracking-widest text-gray-400">To</label>
            <input
              type="date"
              value={filters.to}
              min={filters.from || undefined}
              onChange={event => patch({ to: event.target.value })}
              className="h-10 border border-gray-300 bg-white px-3 text-sm font-body outline-none focus:border-primary"
            />
          </div>
          {(filters.from || filters.to) && (
            <button
              type="button"
              onClick={() => patch({ from: '', to: '' })}
              className="h-10 border border-gray-300 px-3 text-xs font-body uppercase tracking-wider text-gray-500 transition-colors hover:bg-gray-50"
            >
              All time
            </button>
          )}
        </div>
        <div className="mt-3">
          <label className="mb-1 block text-[10px] font-body uppercase tracking-widest text-gray-400">Search</label>
          <Input
            placeholder="Search name, email, or phone…"
            value={search}
            onChange={event => setSearch(event.target.value)}
          />
        </div>
      </div>

      <div className="mb-5 flex gap-5 border-b border-gray-200" role="tablist" aria-label="Customer views">
        {[
          ['directory', 'Customer directory'],
          ['delivery-areas', 'Delivery areas'],
        ].map(([tab, label]) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            onClick={() => setActiveTab(tab as CustomerTab)}
            className={`-mb-px border-b-2 px-0.5 pb-2.5 text-sm font-body transition-colors ${activeTab === tab ? 'border-primary text-gray-800' : 'border-transparent text-gray-400 hover:text-gray-600'}`}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'delivery-areas' ? (
        <DeliveryAreasPanel
          search={debouncedSearch}
          dateFrom={hasCompleteDateRange ? filters.from : undefined}
          dateTo={hasCompleteDateRange ? filters.to : undefined}
        />
      ) : loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<CustomerSummary>
          rows={customers}
          rowKey={customer => customer.id}
          stickyHeader
          sort={sort}
          onSortChange={next => {
            setSort(next);
            setPage(1);
          }}
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
            {
              header: 'Orders',
              className: 'text-center',
              sortable: true,
              sortKey: 'order_count',
              render: customer => customer.order_count,
            },
            {
              header: 'First order',
              sortable: true,
              sortKey: 'earliest_order_at',
              render: customer => customer.earliest_order_at
                ? formatDate(customer.earliest_order_at)
                : '—',
            },
            {
              header: 'Last order',
              sortable: true,
              sortKey: 'latest_order_at',
              render: customer => customer.latest_order_at
                ? formatDate(customer.latest_order_at)
                : '—',
            },
            {
              header: 'Revenue',
              className: 'text-right',
              sortable: true,
              sortKey: 'total_revenue',
              render: customer => formatCurrency(customer.total_revenue),
            },
            {
              header: 'AOV',
              className: 'text-right',
              sortable: true,
              sortKey: 'aov',
              render: customer => formatCurrency(customer.aov),
            },
          ]}
        />
      )}

      {activeTab === 'directory' && (
        <Pagination
          page={page}
          pages={pages}
          total={total}
          perPage={perPage}
          onPageChange={setPage}
          onPerPageChange={setPerPage}
          label="customers"
        />
      )}

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
                  {
                    header: 'Channel',
                    render: order => order.courier ? (
                      <CourierLogo courier={order.courier} size={20} showName />
                    ) : order.order_channel_code ? (
                      <span className="inline-flex items-center gap-1.5">
                        <CourierMark code={order.order_channel_code} size={18} />
                        {order.order_channel}
                      </span>
                    ) : order.order_channel,
                  },
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
