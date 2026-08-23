'use client';

import { useCallback, useEffect, useState } from 'react';
import { grubopsApi, ApiError } from '@/lib/api';
import type {
  GrubOpsLocation,
  GrubOpsMapping,
  GrubOpsOrderRow,
  GrubOpsSyncSummary,
} from '@/lib/types';
import { Badge, Button, Input, LoadError, Pagination, Select, Spinner, TabBar } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { useToast } from '@/components/ui/feedback';
import { formatDateTime } from '@/lib/utils';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';

/**
 * What the aggregators are told when something runs out.
 *
 * Marking an item out on the register takes it off Noon, Talabat and Deliveroo
 * too, instead of somebody having to open GrubOps and say it a second time.
 * Two things on this screen control that.
 *
 * **Branches** is the switch, one per shop. It is on where the register is
 * live and off everywhere else, because a branch whose staff are not marking
 * things out on the terminal has nothing true to say about its stock — leaving
 * it on would push a permanent "everything is available" over whatever that
 * counter maintains in GrubOps by hand.
 *
 * **Items** is the join between the two menus. GrubOps knows its items by its
 * own ids and nothing links them to ours, so the map is built by matching
 * names — which is a guess. Nothing is ever sent for a row until somebody has
 * approved it here, so a wrong guess is a row waiting in this queue rather
 * than the wrong cake disappearing from Talabat.
 *
 * Press **Sync from GrubOps** after a menu change. It only ever adds
 * suggestions; a row already approved or corrected by hand is left alone.
 */

type Tab = 'pending' | 'approved' | 'branches' | 'orders';

const TYPE_OPTIONS = [
  { value: 'RECIPE', label: 'Recipe (a product)' },
  { value: 'MODIFIER', label: 'Modifier (an option)' },
  { value: 'NESTED_MODIFIER', label: 'Nested modifier' },
];

export default function GrubOpsPage() {
  const toast = useToast();

  const [tab, setTab] = useState<Tab>('pending');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [search, setSearch] = useState('');
  const [sortAlpha, setSortAlpha] = useState(false);
  const debouncedSearch = useDebouncedValue(search);

  const [rows, setRows] = useState<GrubOpsMapping[]>([]);
  const [total, setTotal] = useState(0);
  const [approvedCount, setApprovedCount] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [locations, setLocations] = useState<GrubOpsLocation[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [summary, setSummary] = useState<GrubOpsSyncSummary | null>(null);
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

  const loadMappings = useCallback(async () => {
    if (tab === 'branches') return;
    setLoading(true);
    setError(null);
    try {
      const data = await grubopsApi.mappings({
        approved: tab === 'approved',
        search: debouncedSearch || undefined,
        sort: sortAlpha ? 'name' : 'queue',
        page,
        page_size: perPage,
      });
      setRows(data.items);
      setTotal(data.total);
      setApprovedCount(data.approved_count);
      setPendingCount(data.pending_count);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load the item map');
    } finally {
      setLoading(false);
    }
  }, [tab, page, perPage, debouncedSearch, sortAlpha]);

  useEffect(() => {
    void loadLocations();
  }, [loadLocations]);

  useEffect(() => {
    void loadMappings();
  }, [loadMappings]);

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

  const onSync = async () => {
    setSyncing(true);
    setSummary(null);
    try {
      const result = await grubopsApi.sync();
      setSummary(result);
      toast.success(
        `${result.created} new suggestion${result.created === 1 ? '' : 's'}, ` +
          `${result.refreshed} refreshed`,
      );
      await loadMappings();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'GrubOps could not be read');
    } finally {
      setSyncing(false);
    }
  };

  const patch = async (row: GrubOpsMapping, data: object, done: string) => {
    try {
      await grubopsApi.updateMapping(row.id, data);
      toast.success(done);
      await loadMappings();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not save that');
    }
  };

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

  const columns: DataColumn<GrubOpsMapping>[] = [
    {
      header: 'Ours',
      priority: 'primary',
      render: r => (
        <div>
          <div className="font-medium">{r.mm_name ?? '—'}</div>
          {r.mm_parent_name && (
            <div className="text-xs text-gray-500">on {r.mm_parent_name}</div>
          )}
        </div>
      ),
    },
    {
      header: 'GrubOps',
      priority: 'secondary',
      render: r => (
        <div>
          <div>{r.grubops_name ?? '—'}</div>
          <div className="font-mono text-xs text-gray-500">
            {r.grubops_recipe_id ?? r.grubops_modifier_id ?? '—'}
          </div>
        </div>
      ),
    },
    {
      header: 'Type',
      render: r => <Badge variant="neutral">{r.grubops_type}</Badge>,
    },
    {
      header: 'Match',
      render: r =>
        r.match_method === 'manual' ? (
          <Badge variant="info">by hand</Badge>
        ) : r.match_method === 'exact' ? (
          <Badge variant="success">exact</Badge>
        ) : (
          // The number is the point of this column: a 0.83 is a guess worth
          // reading twice, and a 1.00 barely needs looking at.
          <Badge variant={(r.match_score ?? 0) >= 95 ? 'success' : 'warning'}>
            {r.match_score != null ? `${r.match_score.toFixed(0)}%` : 'fuzzy'}
          </Badge>
        ),
    },
    {
      header: 'Last sent',
      render: r =>
        r.last_error ? (
          <span className="text-xs text-red-600" title={r.last_error}>
            failing
          </span>
        ) : r.last_pushed_at ? (
          <span className="text-xs text-gray-500">{formatDateTime(r.last_pushed_at)}</span>
        ) : (
          <span className="text-xs text-gray-400">never</span>
        ),
    },
  ];

  const pages = Math.max(1, Math.ceil(total / perPage));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">GrubOps</h1>
          <p className="mt-1 max-w-2xl text-sm text-gray-600">
            Marking an item out of stock on the register takes it off Noon, Talabat and
            Deliveroo too. Nothing is sent for an item until its mapping is approved
            here, and only branches switched on below are synced.
          </p>
        </div>
        <Button onClick={onSync} loading={syncing}>
          Sync from GrubOps
        </Button>
      </div>

      {summary && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm">
          <p className="font-medium">
            {summary.created} new, {summary.refreshed} refreshed
          </p>
          {summary.unmatched_ours.length > 0 && (
            <p className="mt-2 text-gray-600">
              <span className="font-medium">Ours with no match on GrubOps:</span>{' '}
              {summary.unmatched_ours.join(', ')}
            </p>
          )}
          {summary.unmatched_theirs.length > 0 && (
            <p className="mt-2 text-gray-600">
              <span className="font-medium">Theirs with no match here:</span>{' '}
              {summary.unmatched_theirs.join(', ')}
            </p>
          )}
          {summary.errors.map(e => (
            <p key={e} className="mt-2 text-red-600">
              {e}
            </p>
          ))}
        </div>
      )}

      <TabBar
        tabs={[
          { key: 'pending', label: 'Needs approval', count: pendingCount },
          { key: 'approved', label: 'Approved', count: approvedCount },
          { key: 'branches', label: 'Branches', count: locations.length },
          { key: 'orders', label: 'Ingested orders', count: orderTotal },
        ]}
        active={tab}
        onChange={key => {
          setTab(key as Tab);
          setPage(1);
        }}
      />

      {/* Search + alphabetical sort — on every tab but Branches (a short card
          list that needs neither). The search runs in SQL across the whole set,
          not just the page on screen. */}
      {tab !== 'branches' && (
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="flex-1 min-w-[16rem] max-w-sm">
            <Input
              placeholder={
                tab === 'orders'
                  ? 'Search external id, channel, status…'
                  : 'Search our name, GrubOps name or id…'
              }
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

      {error && <LoadError message={error} onRetry={loadMappings} />}

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
      ) : tab === 'orders' ? (
        loading ? (
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
        )
      ) : loading ? (
        <Spinner />
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={r => r.id}
            empty={
              tab === 'pending'
                ? 'Nothing waiting — every suggested mapping has been dealt with.'
                : 'Nothing approved yet. Approve a mapping to start syncing it.'
            }
            actions={row => (
              <div className="flex justify-end gap-2">
                {row.approved ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => patch(row, { approved: false }, 'Mapping withdrawn')}
                  >
                    Withdraw
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    onClick={() => patch(row, { approved: true }, 'Mapping approved')}
                  >
                    Approve
                  </Button>
                )}
              </div>
            )}
            expanded={row => (
              <MappingEditor row={row} onSave={patch} />
            )}
          />
          <Pagination
            page={page}
            pages={pages}
            total={total}
            perPage={perPage}
            onPageChange={setPage}
            onPerPageChange={size => {
              setPerPage(size);
              setPage(1);
            }}
            label="mappings"
          />
        </>
      )}
    </div>
  );
}

/**
 * Correcting a bad guess, without leaving the table.
 *
 * Editing either id marks the row as done by hand, which is what stops the
 * next sync treating it as its own suggestion and refreshing it.
 */
function MappingEditor({
  row,
  onSave,
}: {
  row: GrubOpsMapping;
  onSave: (row: GrubOpsMapping, data: object, done: string) => Promise<void>;
}) {
  const [recipeId, setRecipeId] = useState(row.grubops_recipe_id ?? '');
  const [modifierId, setModifierId] = useState(row.grubops_modifier_id ?? '');
  const [type, setType] = useState(row.grubops_type);
  const [notes, setNotes] = useState(row.notes ?? '');

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Input
        label="GrubOps recipe id"
        value={recipeId}
        onChange={e => setRecipeId(e.target.value)}
      />
      <Input
        label="GrubOps modifier id"
        value={modifierId}
        onChange={e => setModifierId(e.target.value)}
      />
      <Select
        label="Type"
        options={TYPE_OPTIONS}
        value={type}
        onChange={e => setType(e.target.value as GrubOpsMapping['grubops_type'])}
      />
      <Input label="Notes" value={notes} onChange={e => setNotes(e.target.value)} />
      <div className="sm:col-span-2 lg:col-span-4">
        <Button
          variant="secondary"
          size="sm"
          onClick={() =>
            onSave(
              row,
              {
                grubops_recipe_id: recipeId,
                grubops_modifier_id: modifierId,
                grubops_type: type,
                notes,
              },
              'Mapping updated',
            )
          }
        >
          Save changes
        </Button>
      </div>
    </div>
  );
}
