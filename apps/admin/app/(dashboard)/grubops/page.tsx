'use client';

import { useCallback, useEffect, useState } from 'react';
import { grubopsApi, ApiError } from '@/lib/api';
import type { GrubOpsLocation, GrubOpsOrderRow } from '@/lib/types';
import { Badge, Button, Input, LoadError, Pagination, Spinner, TabBar } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { AggregatorTabs } from '../aggregators/AggregatorTabs';

/**
 * The GrubOps-specific config and log, now one tab of the unified Marketplaces
 * screen. The item map itself — which of our items each marketplace's item is —
 * moved to the shared **Item Mappings** tab, because it was never GrubOps-only;
 * what is left here is the two things that are.
 *
 * **Branches** is the switch, one per shop. It is on where the register is
 * live and off everywhere else, because a branch whose staff are not marking
 * things out on the terminal has nothing true to say about its stock — leaving
 * it on would push a permanent "everything is available" over whatever that
 * counter maintains in GrubOps by hand.
 *
 * **Ingested orders** is the log of aggregator orders that arrived through
 * GrubOps, and anything that failed on the way in.
 */

type Tab = 'branches' | 'orders';

export default function GrubOpsPage() {
  const toast = useToast();

  const [tab, setTab] = useState<Tab>('branches');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [search, setSearch] = useState('');
  const [sortAlpha, setSortAlpha] = useState(false);
  const debouncedSearch = useDebouncedValue(search);

  const [locations, setLocations] = useState<GrubOpsLocation[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [orders, setOrders] = useState<GrubOpsOrderRow[]>([]);
  const [orderTotal, setOrderTotal] = useState(0);
  const [orderErrors, setOrderErrors] = useState(0);
  const [orderUnmapped, setOrderUnmapped] = useState(0);

  const loadLocations = useCallback(async () => {
    try {
      setLocations(await grubopsApi.locations());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load branches');
    }
  }, []);

  useEffect(() => {
    void loadLocations();
  }, [loadLocations]);

  const loadOrders = useCallback(async () => {
    if (tab !== 'orders') return;
    setLoading(true);
    setError(null);
    try {
      const data = await grubopsApi.orders({
        search: debouncedSearch || undefined,
        sort: sortAlpha ? 'channel' : 'recent',
        page,
        page_size: perPage,
      });
      setOrders(data.items);
      setOrderTotal(data.total);
      setOrderErrors(data.error_count);
      setOrderUnmapped(data.unmapped_count);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load ingested orders');
    } finally {
      setLoading(false);
    }
  }, [tab, page, perPage, debouncedSearch, sortAlpha]);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  const toggleBranch = async (location: GrubOpsLocation) => {
    try {
      const updated = await grubopsApi.updateLocation(location.id, {
        is_active: !location.is_active,
      });
      setLocations(prev => prev.map(l => (l.id === updated.id ? updated : l)));
      toast.success(
        updated.is_active
          ? `${updated.branch_name} now syncs to GrubOps`
          : `${updated.branch_name} no longer syncs`,
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not change that branch');
    }
  };

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div>
        <h1 className="text-2xl font-semibold">GrubOps</h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-600">
          Marking an item out of stock on the register takes it off Noon, Talabat and
          Deliveroo too. Nothing is sent for an item until its mapping is approved on the
          Item Mappings tab, and only branches switched on below are synced.
        </p>
      </div>

      <TabBar
        tabs={[
          { key: 'branches', label: 'Branches', count: locations.length },
          { key: 'orders', label: 'Ingested orders', count: orderTotal },
        ]}
        active={tab}
        onChange={key => {
          setTab(key as Tab);
          setPage(1);
        }}
      />

      {/* Search + alphabetical sort — on the orders tab only (Branches is a
          short card list that needs neither). The search runs in SQL across the
          whole set, not just the page on screen. */}
      {tab === 'orders' && (
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="flex-1 min-w-[16rem] max-w-sm">
            <Input
              placeholder="Search external id, channel, status…"
              value={search}
              onChange={e => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <Button
            variant={sortAlpha ? 'primary' : 'outline'}
            size="sm"
            onClick={() => {
              setSortAlpha(v => !v);
              setPage(1);
            }}
          >
            <span className="material-icons text-[14px]">sort_by_alpha</span>
            {sortAlpha ? 'Alphabetical' : 'Sort A–Z'}
          </Button>
        </div>
      )}

      {error && <LoadError message={error} onRetry={tab === 'orders' ? loadOrders : loadLocations} />}

      {tab === 'branches' ? (
        <div className="space-y-3">
          {locations.length === 0 && (
            <p className="text-sm text-gray-500">
              No branch is mapped to a GrubOps location yet.
            </p>
          )}
          {locations.map(location => (
            <div
              key={location.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 p-4"
            >
              <div>
                <div className="font-medium">
                  {location.branch_name}{' '}
                  <span className="text-xs text-gray-500">
                    {location.branch_reference}
                  </span>
                </div>
                <div className="font-mono text-xs text-gray-500">
                  {location.grubops_location_id}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <Badge variant={location.is_active ? 'success' : 'neutral'}>
                  {location.is_active ? 'Syncing' : 'Off'}
                </Badge>
                <Button
                  variant={location.is_active ? 'secondary' : 'primary'}
                  size="sm"
                  onClick={() => toggleBranch(location)}
                >
                  {location.is_active ? 'Turn off' : 'Turn on'}
                </Button>
              </div>
            </div>
          ))}
          <p className="pt-2 text-xs text-gray-500">
            Turning a branch on sends its approved out-of-stock items within a couple of
            minutes. Turning one off stops sending — GrubOps keeps whatever it was last
            told, rather than putting the whole menu back on sale.
          </p>
        </div>
      ) : loading ? (
        <Spinner />
      ) : (
        <div className="space-y-3">
          <div className="flex gap-4 text-sm text-gray-600">
            <span>{orderTotal} order(s)</span>
            {orderErrors > 0 && (
              <span className="text-red-600">{orderErrors} with a push error</span>
            )}
            {orderUnmapped > 0 && (
              <span className="text-amber-600">{orderUnmapped} with unmapped lines</span>
            )}
          </div>
          {orders.length === 0 && (
            <p className="text-sm text-gray-500">
              No aggregator orders ingested yet. They appear here once
              GRUBOPS_ORDERS_ENABLED is on and orders start arriving.
            </p>
          )}
          {orders.map(o => (
            <div
              key={o.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 p-4"
            >
              <div>
                <div className="font-medium">
                  {o.source_channel ?? 'Aggregator'}{' '}
                  <span className="text-xs text-gray-500">#{o.external_id}</span>
                </div>
                <div className="font-mono text-xs text-gray-500">
                  {o.mm_order_number ?? 'not created'} · {o.last_grubops_status}
                </div>
                {o.last_push_error && (
                  <div className="text-xs text-red-600">{o.last_push_error}</div>
                )}
              </div>
              <div className="flex items-center gap-2">
                {o.has_unmapped_lines && <Badge variant="warning">unmapped</Badge>}
                {o.mm_order_id && <Badge variant="success">recorded</Badge>}
              </div>
            </div>
          ))}
          <Pagination
            page={page}
            pages={Math.ceil(orderTotal / perPage)}
            total={orderTotal}
            perPage={perPage}
            onPageChange={setPage}
            onPerPageChange={size => {
              setPerPage(size);
              setPage(1);
            }}
            label="orders"
          />
        </div>
      )}
    </div>
  );
}
