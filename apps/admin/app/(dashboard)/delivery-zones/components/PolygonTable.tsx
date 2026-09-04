'use client';

import { useCallback, useMemo, useState } from 'react';

import { deliveryZonesApi, ApiError } from '@/lib/api';
import type { Branch } from '@/lib/pos-types';
import type {
  BatchGroup,
  DeliveryMapVersion,
  DeliveryPricingMode,
  DeliveryZone,
  FulfilmentProvider,
  PolygonPage,
} from '@/lib/types';
import { Badge, Button, Input, LoadError, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { useToast } from '@/components/ui/feedback';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { cn, formatCurrency } from '@/lib/utils';

import {
  AlternatePicker,
  DEFAULT_ALTERNATES,
  PRICING_LABEL,
  PRICING_OPTIONS,
  PROVIDER_BADGE,
  PROVIDER_LABEL,
  PROVIDER_OPTIONS,
} from './provider-labels';

/**
 * The fees-and-couriers table, one map at a time.
 *
 * The zones used to be six per draft and a plain `<table>` inside every version
 * card showed them all. The live map is ~97 per-area zones now — a scroll
 * nobody can find a row in — so this pages, searches, sorts and filters them in
 * SQL through `GET /delivery-zones/polygons`, the same shape the customers list
 * uses. One version is shown at a time, chosen from the switcher; the active
 * map is the default, because editing it in place is now allowed and is the
 * thing somebody usually came here to do.
 *
 * The Map tab still draws the outlines. This is the pricing side of the same
 * data, and carries no geometry.
 */

// The sortable columns, mapped to the API's `sort` values. Kept beside the
// header cells that toggle them so the two cannot drift.
type SortKey =
  | 'name'
  | 'delivery_fee'
  | 'free_delivery_threshold'
  | 'fulfilment_provider'
  | 'display_order';

interface PolygonTableProps {
  versions: DeliveryMapVersion[];
  branches: Branch[];
  batchGroups: BatchGroup[];
  /** A version-level action (copy / publish / delete) is in flight on the page. */
  busy: boolean;
  draftName: string;
  onDraftNameChange: (value: string) => void;
  /** Copy the shown version into a fresh editable draft. */
  onCopy: (sourceVersionId: string) => void;
  onPublish: (version: DeliveryMapVersion) => void;
  onDelete: (version: DeliveryMapVersion) => void;
}

export function PolygonTable({
  versions,
  branches,
  batchGroups,
  busy,
  draftName,
  onDraftNameChange,
  onCopy,
  onPublish,
  onDelete,
}: PolygonTableProps) {
  const toast = useToast();

  // Default to the live map — it is the one most edits are about, now that the
  // backend lets it be edited in place rather than only through a draft.
  const [versionId, setVersionId] = useState(
    () => versions.find(v => v.is_active)?.id ?? versions[0]?.id ?? '',
  );
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search);
  const [provider, setProvider] = useState('');
  const [branchId, setBranchId] = useState('');
  const [batchGroupId, setBatchGroupId] = useState('');
  const [sort, setSort] = useState<SortKey>('display_order');
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc');
  // Which row is open for editing. Only one at a time — a page of half-edited
  // rows is a page of unsaved surprises.
  const [editingId, setEditingId] = useState<string | null>(null);

  const version = versions.find(v => v.id === versionId) ?? null;
  // The live map (now editable in place) and a never-published draft may be
  // changed; a superseded, already-published version is history and stays
  // read-only.
  const editable = version != null && (version.is_active || version.activated_at === null);

  // Server-side everything: a new fetcher identity (any filter/sort/version
  // change) is what `useApiList` reads as "reset to page 1 and reload".
  const fetchPolygons = useCallback(
    (page: number, perPage: number): Promise<PolygonPage> =>
      deliveryZonesApi.listPolygons({
        version_id: versionId || undefined,
        page,
        per_page: perPage,
        search: debouncedSearch || undefined,
        provider: provider || undefined,
        branch_id: branchId || undefined,
        batch_group_id: batchGroupId || undefined,
        sort,
        direction,
      }),
    [versionId, debouncedSearch, provider, branchId, batchGroupId, sort, direction],
  );

  const {
    items: zones, total, pages, page, perPage, setPage, setPerPage,
    loading, loadError, refetch,
  } = useApiList<DeliveryZone>({ paginate: 'server', fetch: fetchPolygons });

  /** Click a sortable header: toggle direction if it's already the sort key. */
  function toggleSort(key: SortKey) {
    if (sort === key) {
      setDirection(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSort(key);
      setDirection('asc');
    }
    setEditingId(null);
  }

  /** Commit one row's edits, then reload the page it sits on. */
  async function saveZone(
    zoneId: string,
    data: Parameters<typeof deliveryZonesApi.updateZone>[1],
  ) {
    try {
      await deliveryZonesApi.updateZone(zoneId, data);
      setEditingId(null);
      await refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    }
  }

  const branchLabel = useMemo(() => {
    const byId = new Map(branches.map(b => [b.id, b] as const));
    return (id: string | null) => {
      if (!id) return '—';
      const b = byId.get(id);
      return b ? b.reference : '—';
    };
  }, [branches]);

  const batchGroupLabel = useMemo(() => {
    const byId = new Map(batchGroups.map(g => [g.id, g.name] as const));
    return (id: string | null) => (id ? byId.get(id) ?? '—' : '—');
  }, [batchGroups]);

  const versionOptions = versions.map(v => ({
    value: v.id,
    label: `${v.name} · ${v.is_active ? 'Live' : v.activated_at ? 'Archived' : 'Draft'}`,
  }));

  return (
    <div className="bg-white border border-gray-200 p-4">
      <LoadError message={loadError} onRetry={refetch} />

      {/* ── Version switcher + management ─────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-2 mb-4">
        <div className="w-64">
          <Select
            label="Map"
            value={versionId}
            options={versionOptions}
            onChange={e => { setVersionId(e.target.value); setEditingId(null); setPage(1); }}
          />
        </div>
        {version && (
          version.is_active ? (
            <Badge variant="success">Live</Badge>
          ) : version.activated_at ? (
            <Badge variant="neutral">Archived</Badge>
          ) : (
            <Badge variant="warning">Draft</Badge>
          )
        )}
        <div className="flex-1" />
        <Input
          label="Copy to a new draft"
          value={draftName}
          onChange={e => onDraftNameChange(e.target.value)}
          placeholder="e.g. Ramadan pricing"
          className="w-52"
        />
        <Button size="sm" variant="ghost" onClick={() => version && onCopy(version.id)} disabled={busy || !version}>
          <span className="material-icons text-[14px]">content_copy</span>
          Copy
        </Button>
        {version && !version.is_active && version.activated_at === null && (
          <>
            <Button size="sm" onClick={() => onPublish(version)} disabled={busy}>
              <span className="material-icons text-[14px]">publish</span>
              Publish
            </Button>
            <Button size="sm" variant="danger" onClick={() => onDelete(version)} disabled={busy}>
              <span className="material-icons text-[14px]">delete</span>
              Delete
            </Button>
          </>
        )}
      </div>

      {/* ── Search + filters ──────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2 mb-4">
        <div className="w-56">
          <Input
            placeholder="Search by zone name…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="w-44">
          <Select
            value={provider}
            options={[{ value: '', label: 'All couriers' }, ...PROVIDER_OPTIONS]}
            onChange={e => setProvider(e.target.value)}
          />
        </div>
        <div className="w-52">
          <Select
            value={branchId}
            options={[
              { value: '', label: 'All kitchens' },
              ...branches.map(b => ({ value: b.id, label: `${b.reference} · ${b.name}` })),
            ]}
            onChange={e => setBranchId(e.target.value)}
          />
        </div>
        <div className="w-52">
          <Select
            value={batchGroupId}
            options={[
              { value: '', label: 'All runs' },
              ...batchGroups.map(g => ({ value: g.id, label: g.name })),
            ]}
            onChange={e => setBatchGroupId(e.target.value)}
          />
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<DeliveryZone>
          rows={zones}
          rowKey={z => z.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              No zones match these filters.
            </p>
          }
          actions={
            editable
              ? z => (
                  <RowAction onClick={() => setEditingId(editingId === z.id ? null : z.id)}>
                    {editingId === z.id ? 'Close' : 'Edit'}
                  </RowAction>
                )
              : undefined
          }
          expanded={
            editable
              ? z =>
                  editingId === z.id ? (
                    <ZoneEditForm
                      zone={z}
                      branches={branches}
                      onCancel={() => setEditingId(null)}
                      onSave={data => saveZone(z.id, data)}
                    />
                  ) : null
              : undefined
          }
          columns={[
            {
              header: 'Zone',
              priority: 'primary',
              headerRender: () => <SortHeader label="Zone" col="name" sort={sort} direction={direction} onSort={toggleSort} />,
              render: z => (
                <div>
                  <div className="text-xs font-body text-gray-800">{z.name}</div>
                  <div className="text-[11px] font-body text-gray-400">
                    {z.point_count.toLocaleString()} points
                  </div>
                </div>
              ),
            },
            {
              header: 'Fulfilled by',
              headerRender: () => <SortHeader label="Fulfilled by" col="fulfilment_provider" sort={sort} direction={direction} onSort={toggleSort} />,
              render: z => (
                <Badge variant={PROVIDER_BADGE[z.fulfilment_provider] ?? 'neutral'}>
                  {PROVIDER_LABEL[z.fulfilment_provider] ?? z.fulfilment_provider}
                </Badge>
              ),
            },
            {
              header: 'Fee',
              className: 'text-right',
              headerRender: () => <SortHeader label="Fee" col="delivery_fee" sort={sort} direction={direction} onSort={toggleSort} align="right" />,
              render: z =>
                z.pricing_mode === 'dynamic' ? (
                  <span className="text-xs font-body text-gray-400">Per pin</span>
                ) : (
                  <span className="text-xs font-body text-gray-700">{formatCurrency(z.delivery_fee)}</span>
                ),
            },
            {
              header: 'Free over',
              headerRender: () => <SortHeader label="Free over" col="free_delivery_threshold" sort={sort} direction={direction} onSort={toggleSort} />,
              render: z => (
                <Badge variant={z.free_delivery_eligible ? 'success' : 'neutral'}>
                  {!z.free_delivery_eligible
                    ? 'Always charged'
                    : z.free_delivery_threshold > 0
                      ? `Free over ${formatCurrency(z.free_delivery_threshold)}`
                      : 'Always free'}
                </Badge>
              ),
            },
            {
              header: 'Baked at',
              render: z => <span className="text-xs font-body text-gray-700">{branchLabel(z.branch_id)}</span>,
            },
            {
              header: 'Run',
              render: z => <span className="text-xs font-body text-gray-500">{batchGroupLabel(z.batch_group_id)}</span>,
            },
            {
              header: 'Order',
              priority: 'desktop',
              className: 'text-right',
              headerRender: () => <SortHeader label="Order" col="display_order" sort={sort} direction={direction} onSort={toggleSort} align="right" />,
              render: z => <span className="text-xs font-body text-gray-400">{z.display_order}</span>,
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
        label="zones"
      />
    </div>
  );
}

/** A clickable, arrow-bearing column header. Desktop only — cards have no headers. */
function SortHeader({
  label,
  col,
  sort,
  direction,
  onSort,
  align,
}: {
  label: string;
  col: SortKey;
  sort: SortKey;
  direction: 'asc' | 'desc';
  onSort: (col: SortKey) => void;
  align?: 'right';
}) {
  const active = sort === col;
  return (
    <button
      type="button"
      onClick={() => onSort(col)}
      className={cn(
        'inline-flex items-center gap-0.5 uppercase tracking-widest hover:text-gray-700',
        align === 'right' && 'flex-row-reverse',
        active ? 'text-gray-700' : 'text-gray-500',
      )}
    >
      {label}
      <span className="material-icons text-[14px] leading-none">
        {active ? (direction === 'asc' ? 'arrow_drop_up' : 'arrow_drop_down') : 'unfold_more'}
      </span>
    </button>
  );
}

/**
 * The inline editor for one zone, shown under the row it edits.
 *
 * A local draft rather than a patch-per-keystroke: the fields are committed
 * together on Save so the open row does not reload out from under the person
 * editing it after every change. Mirrors the field set the old `ZoneRow`
 * offered — fee, pricing mode, courier and its alternates, kitchen, and the
 * free-delivery offer — and shares its courier vocabulary.
 */
function ZoneEditForm({
  zone,
  branches,
  onCancel,
  onSave,
}: {
  zone: DeliveryZone;
  branches: Branch[];
  onCancel: () => void;
  onSave: (data: {
    delivery_fee?: number;
    pricing_mode?: DeliveryPricingMode;
    free_delivery_eligible?: boolean;
    free_delivery_threshold?: number;
    fulfilment_provider?: FulfilmentProvider;
    alternate_providers?: FulfilmentProvider[];
    branch_id?: string | null;
  }) => void;
}) {
  const [pricingMode, setPricingMode] = useState<DeliveryPricingMode>(zone.pricing_mode);
  const [fee, setFee] = useState(String(zone.delivery_fee));
  const [preferred, setPreferred] = useState<FulfilmentProvider>(zone.fulfilment_provider);
  const [alternates, setAlternates] = useState<FulfilmentProvider[]>(zone.alternate_providers ?? []);
  const [branch, setBranch] = useState(zone.branch_id ?? '');
  const [eligible, setEligible] = useState(zone.free_delivery_eligible);
  const [threshold, setThreshold] = useState(String(zone.free_delivery_threshold));
  const [feeError, setFeeError] = useState('');
  const [thresholdError, setThresholdError] = useState('');

  const dynamic = pricingMode === 'dynamic';

  function handleSave() {
    const patch: Parameters<typeof onSave>[0] = {
      pricing_mode: pricingMode,
      fulfilment_provider: preferred,
      alternate_providers: alternates,
      free_delivery_eligible: eligible,
      // Null hands the zone back to the default pickup branch.
      branch_id: branch || null,
    };

    // A courier-priced zone has no fee of its own — don't send one.
    if (!dynamic) {
      const parsedFee = Number(fee);
      if (!Number.isFinite(parsedFee) || parsedFee < 0) {
        setFeeError('Enter a fee of zero or more.');
        return;
      }
      patch.delivery_fee = parsedFee;
    }

    if (eligible) {
      const parsedThreshold = Number(threshold);
      // Zero is a real value — free at any basket — so guard "not a number"
      // and "negative", never falsiness.
      if (!Number.isFinite(parsedThreshold) || parsedThreshold < 0) {
        setThresholdError('Enter a threshold of zero or more.');
        return;
      }
      patch.free_delivery_threshold = parsedThreshold;
    }

    onSave(patch);
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 text-xs font-body">
      <label className="flex flex-col gap-1">
        <span className="text-gray-500 uppercase tracking-widest text-[11px]">Priced by</span>
        <Select
          value={pricingMode}
          options={PRICING_OPTIONS}
          onChange={e => setPricingMode(e.target.value as DeliveryPricingMode)}
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-gray-500 uppercase tracking-widest text-[11px]">Fee</span>
        {dynamic ? (
          <span className="text-gray-400 py-2">
            {PRICING_LABEL.dynamic} — charged per pin, no fixed fee.
          </span>
        ) : (
          <>
            <input
              value={fee}
              onChange={e => { setFee(e.target.value); setFeeError(''); }}
              inputMode="decimal"
              className="w-full px-3 py-2 bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30"
            />
            {feeError && <span className="text-red-500">{feeError}</span>}
          </>
        )}
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-gray-500 uppercase tracking-widest text-[11px]">Baked at</span>
        <Select
          value={branch}
          placeholder="Default pickup branch"
          options={branches.map(b => ({ value: b.id, label: `${b.reference} · ${b.name}` }))}
          onChange={e => setBranch(e.target.value)}
        />
      </label>

      <div className="flex flex-col gap-1 sm:col-span-2 lg:col-span-1">
        <span className="text-gray-500 uppercase tracking-widest text-[11px]">Fulfilled by</span>
        <Select
          value={preferred}
          options={PROVIDER_OPTIONS}
          onChange={e => {
            // Changing the preferred courier resets the alternates rather than
            // keeping them: the outgoing courier is now a good alternate and the
            // incoming one cannot be its own, so a fresh default beats a
            // half-wrong carried-over list.
            const next = e.target.value as FulfilmentProvider;
            setPreferred(next);
            setAlternates(DEFAULT_ALTERNATES[next] ?? []);
          }}
        />
        <AlternatePicker preferred={preferred} chosen={alternates} onChange={setAlternates} />
        {zone.batch_group_id && preferred !== zone.fulfilment_provider && (
          <span className="text-[11px] text-gray-500">
            on a shared run — changing the courier takes it off
          </span>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-gray-500 uppercase tracking-widest text-[11px]">Free delivery</span>
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={eligible}
            onChange={e => setEligible(e.target.checked)}
            className="w-4 h-4 accent-primary cursor-pointer"
            aria-label={`Free delivery in ${zone.name}`}
          />
          <input
            type="number"
            min="0"
            step="0.01"
            value={threshold}
            disabled={!eligible}
            onChange={e => { setThreshold(e.target.value); setThresholdError(''); }}
            title="The basket that earns free delivery here. 0 means free at any basket."
            className="w-24 border border-gray-300 px-2 py-1 text-right disabled:bg-gray-50 disabled:text-gray-400"
            aria-label={`Free delivery threshold for ${zone.name}`}
          />
        </div>
        {thresholdError && <span className="text-red-500">{thresholdError}</span>}
      </div>

      <div className="flex items-end justify-end gap-2 sm:col-span-2 lg:col-span-3">
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button size="sm" onClick={handleSave}>Save</Button>
      </div>
    </div>
  );
}
