'use client';

import { useCallback, useEffect, useState } from 'react';

import { itemMappingsApi, ApiError } from '@/lib/api';
import type { Schemas } from '@mm/types';
import { Badge, Button, Input, LoadError, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { useToast } from '@/components/ui/feedback';
import { formatDateTime } from '@/lib/utils';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { AggregatorTabs } from '../AggregatorTabs';

// Row/summary/update shapes are the generated contract (rule 8).
type ItemMappingRow = Schemas['ItemMappingResponse'];
type ItemMappingUpdate = Schemas['ItemMappingUpdate'];
type ItemMappingSyncSummary = Schemas['ItemMappingSyncSummary'];

/**
 * The one review queue for every external-system item map.
 *
 * GrubOps and each aggregator (Keeta, Deliveroo, Talabat, noon, Careem) all
 * name their menu items by their own ids; nothing links them to ours, so the
 * map is built by matching names — a guess. Nothing is ever pushed for a row
 * until somebody has approved it here, so a wrong guess is a row waiting in
 * this queue rather than the wrong item disappearing from a marketplace.
 *
 * **Re-sync GrubOps** re-reads their menu and proposes mappings for anything
 * new. It is GrubOps-only (the aggregators have no menu feed to pull), so the
 * button shows only when the GrubOps system is selected. It only ever adds
 * suggestions; a row already approved or corrected by hand is left alone.
 */

// `''` is "all systems" — the empty value `buildQs` drops rather than sends,
// same as the reconciliation screen.
const SYSTEM_OPTIONS = [
  { value: '', label: 'All systems' },
  { value: 'grubops', label: 'GrubOps' },
  { value: 'keeta', label: 'Keeta' },
  { value: 'deliveroo', label: 'Deliveroo' },
  { value: 'talabat', label: 'Talabat' },
  { value: 'noon', label: 'noon' },
  { value: 'careem', label: 'Careem' },
];

const APPROVED_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'pending', label: 'Needs approval' },
  { value: 'approved', label: 'Approved' },
];

// Our side of the join: a catalogue product or a modifier option. Mirrors the
// `ck_external_item_map_kind` CHECK on the table.
const KIND_OPTIONS = [
  { value: 'product', label: 'Product' },
  { value: 'option', label: 'Modifier option' },
];

/** Prettify a system code for a badge without a lookup table. */
function systemName(code: string): string {
  return code === 'noon' ? 'noon' : code === 'grubops' ? 'GrubOps' : code.charAt(0).toUpperCase() + code.slice(1);
}

export default function ItemMappingsPage() {
  const toast = useToast();

  const [system, setSystem] = useState('');
  const [approved, setApproved] = useState('');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [search, setSearch] = useState('');
  const [sortAlpha, setSortAlpha] = useState(false);
  const debouncedSearch = useDebouncedValue(search);

  const [rows, setRows] = useState<ItemMappingRow[]>([]);
  const [total, setTotal] = useState(0);
  const [approvedCount, setApprovedCount] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [summary, setSummary] = useState<ItemMappingSyncSummary | null>(null);
  // Which row's inline editor is open. One at a time — a table full of open
  // editors is noise, and only one row is ever being corrected.
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await itemMappingsApi.list({
        system: system || undefined,
        approved: approved === '' ? undefined : approved === 'approved',
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
  }, [system, approved, page, perPage, debouncedSearch, sortAlpha]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSync = async () => {
    setSyncing(true);
    setSummary(null);
    try {
      const result = await itemMappingsApi.sync('grubops');
      setSummary(result);
      toast.success(
        `${result.created} new suggestion${result.created === 1 ? '' : 's'}, ` +
          `${result.refreshed} refreshed`,
      );
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'GrubOps could not be read');
    } finally {
      setSyncing(false);
    }
  };

  const patch = async (row: ItemMappingRow, data: ItemMappingUpdate, done: string) => {
    try {
      await itemMappingsApi.update(row.id, data);
      toast.success(done);
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not save that');
    }
  };

  const columns: DataColumn<ItemMappingRow>[] = [
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
      header: 'External',
      priority: 'secondary',
      render: r => (
        <div>
          <div className="flex items-center gap-2">
            <Badge variant="neutral">{systemName(r.system)}</Badge>
            <span>{r.external_name ?? '—'}</span>
          </div>
          <div className="font-mono text-xs text-gray-500">
            {r.external_ref}
            {r.external_sub_ref && <span className="text-gray-400"> · {r.external_sub_ref}</span>}
          </div>
        </div>
      ),
    },
    {
      header: 'Type',
      render: r =>
        r.external_type ? <Badge variant="neutral">{r.external_type}</Badge> : <span className="text-gray-300">—</span>,
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
            {r.match_score != null ? `${r.match_score.toFixed(0)}%` : r.match_method || 'fuzzy'}
          </Badge>
        ),
    },
    {
      header: 'Status',
      render: r => (
        <div className="space-y-1">
          <Badge variant={r.approved ? 'success' : 'neutral'}>
            {r.approved ? 'approved' : 'pending'}
          </Badge>
          {/* The push status is GrubOps-only — the aggregators are read, not
              pushed to, so those rows have nothing to show here. */}
          {r.system === 'grubops' &&
            (r.last_error ? (
              <div className="text-xs text-red-600" title={r.last_error}>
                failing
              </div>
            ) : r.last_pushed_at ? (
              <div className="text-xs text-gray-500">{formatDateTime(r.last_pushed_at)}</div>
            ) : (
              <div className="text-xs text-gray-400">never sent</div>
            ))}
        </div>
      ),
    },
  ];

  const pages = Math.max(1, Math.ceil(total / perPage));

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Item Mappings</h1>
          <p className="mt-1 max-w-2xl text-sm text-gray-600">
            The join between our menu and every marketplace&rsquo;s. Nothing is pushed
            for an item until its mapping is approved here. {pendingCount} awaiting
            approval, {approvedCount} approved.
          </p>
        </div>
        {system === 'grubops' && (
          <Button onClick={onSync} loading={syncing}>
            Re-sync GrubOps
          </Button>
        )}
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

      {/* Filters: system, status, search, and the alphabetical sort. The search
          runs in SQL across the whole set, not just the page on screen. */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-44">
          <Select
            value={system}
            onChange={e => {
              setSystem(e.target.value);
              setPage(1);
            }}
            options={SYSTEM_OPTIONS}
          />
        </div>
        <div className="w-44">
          <Select
            value={approved}
            onChange={e => {
              setApproved(e.target.value);
              setPage(1);
            }}
            options={APPROVED_OPTIONS}
          />
        </div>
        <div className="flex-1 min-w-[16rem] max-w-sm">
          <Input
            placeholder="Search our name, the external name or id…"
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

      {error && <LoadError message={error} onRetry={load} />}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={r => r.id}
            empty={
              <p className="py-16 text-center text-sm text-gray-400 font-body">
                No item mappings for these filters.
              </p>
            }
            actions={row => (
              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setExpandedId(id => (id === row.id ? null : row.id))}
                >
                  {expandedId === row.id ? 'Close' : 'Edit'}
                </Button>
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
            expanded={row =>
              expandedId === row.id ? (
                <MappingEditor
                  row={row}
                  onSave={async (r, data, done) => {
                    await patch(r, data, done);
                    setExpandedId(null);
                  }}
                />
              ) : null
            }
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
 * Points the row at a catalogue product (`product_id`) or option
 * (`modifier_option_id`), with the `mm_kind` that says which; and edits the
 * external identity. Either kind of edit marks the row `manual`, which is what
 * stops the next sync treating it as its own suggestion and refreshing it.
 */
function MappingEditor({
  row,
  onSave,
}: {
  row: ItemMappingRow;
  onSave: (row: ItemMappingRow, data: ItemMappingUpdate, done: string) => Promise<void>;
}) {
  const [kind, setKind] = useState(row.mm_kind || 'product');
  const [productId, setProductId] = useState(row.product_id ?? '');
  const [modifierOptionId, setModifierOptionId] = useState(row.modifier_option_id ?? '');
  const [externalRef, setExternalRef] = useState(row.external_ref ?? '');
  const [externalSubRef, setExternalSubRef] = useState(row.external_sub_ref ?? '');
  const [externalChildRef, setExternalChildRef] = useState(row.external_child_ref ?? '');
  const [externalType, setExternalType] = useState(row.external_type ?? '');
  const [notes, setNotes] = useState(row.notes ?? '');

  // A blank string is "no id", which the API stores as null rather than "".
  const orNull = (v: string) => (v.trim() === '' ? null : v.trim());

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <Select
        label="Kind"
        options={KIND_OPTIONS}
        value={kind}
        onChange={e => setKind(e.target.value)}
      />
      <Input
        label="Product id"
        value={productId}
        onChange={e => setProductId(e.target.value)}
        placeholder="Our catalogue product"
      />
      <Input
        label="Modifier option id"
        value={modifierOptionId}
        onChange={e => setModifierOptionId(e.target.value)}
        placeholder="Our modifier option"
      />
      <Input
        label="External ref"
        value={externalRef}
        onChange={e => setExternalRef(e.target.value)}
      />
      <Input
        label="External sub-ref"
        value={externalSubRef}
        onChange={e => setExternalSubRef(e.target.value)}
      />
      <Input
        label="External child ref"
        value={externalChildRef}
        onChange={e => setExternalChildRef(e.target.value)}
      />
      <Input
        label="External type"
        value={externalType}
        onChange={e => setExternalType(e.target.value)}
      />
      <Input label="Notes" value={notes} onChange={e => setNotes(e.target.value)} />
      <div className="sm:col-span-2 lg:col-span-3">
        <Button
          variant="secondary"
          size="sm"
          onClick={() =>
            onSave(
              row,
              {
                mm_kind: kind,
                product_id: orNull(productId),
                modifier_option_id: orNull(modifierOptionId),
                external_ref: orNull(externalRef),
                external_sub_ref: orNull(externalSubRef),
                external_child_ref: orNull(externalChildRef),
                external_type: orNull(externalType),
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
