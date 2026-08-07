'use client';

import { useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type {
  DeliveryBatch,
  DeliveryMapVersion,
  DeliveryPricingMode,
  DeliveryZone,
  DeliveryZoneMap,
  BatchGroup,
  DeliverySettings,
  FulfilmentProvider,
} from '@/lib/types';
import { Badge, Button, Input, Select, Spinner, TabBar } from '@/components/ui';
import { BatchWindows } from '@/components/delivery/BatchWindows';
import { ZoneMap } from '@/components/delivery/ZoneMap';
import { cn, formatCurrency, formatDate } from '@/lib/utils';

// Named rather than called "Courier API": there are two of them now, they cost
// different amounts, and only one can be batched — so which is which matters.
const PROVIDER_LABEL: Record<FulfilmentProvider, string> = {
  lalamove: 'Lalamove',
  noon_send: 'noon Send',
  third_party: 'Third party',
};

const PROVIDER_OPTIONS = [
  { value: 'lalamove', label: PROVIDER_LABEL.lalamove },
  { value: 'noon_send', label: PROVIDER_LABEL.noon_send },
  { value: 'third_party', label: PROVIDER_LABEL.third_party },
];

const PROVIDER_BADGE: Record<FulfilmentProvider, 'info' | 'success' | 'neutral'> = {
  lalamove: 'info',
  noon_send: 'success',
  third_party: 'neutral',
};

const PRICING_LABEL: Record<DeliveryPricingMode, string> = {
  static: 'Fixed fee',
  dynamic: 'Courier price',
};

const PRICING_OPTIONS = [
  { value: 'static', label: PRICING_LABEL.static },
  { value: 'dynamic', label: PRICING_LABEL.dynamic },
];

export default function DeliveryZonesPage() {
  const [versions, setVersions] = useState<DeliveryMapVersion[]>([]);
  const [settings, setSettings] = useState<DeliverySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState('');
  const [tab, setTab] = useState('map');
  const [zoneMap, setZoneMap] = useState<DeliveryZoneMap | null>(null);
  const [batches, setBatches] = useState<DeliveryBatch[]>([]);
  // Which kitchen bakes a zone's orders. Needed here rather than on the branch
  // page because the choice belongs to the shape, not to the branch.
  const [branches, setBranches] = useState<Branch[]>([]);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [v, s, m, b, br] = await Promise.all([
        deliveryZonesApi.listVersions(),
        deliveryZonesApi.getSettings(),
        deliveryZonesApi.map(),
        deliveryZonesApi.listBatches({ limit: 50 }),
        branchesApi.list(),
      ]);
      setVersions(v);
      setSettings(s);
      setZoneMap(m);
      setBatches(b);
      setBranches(br.filter(x => x.is_active && !x.deleted_at));
      setOpenId(current => current ?? v.find(x => x.is_active)?.id ?? v[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load delivery maps.');
    } finally {
      setLoading(false);
    }
  }

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await load();
    } catch (err) {
      alert(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function createDraft(sourceId: string) {
    const name = draftName.trim();
    if (!name) {
      alert('Give the draft a name so it can be told apart from the live map.');
      return;
    }
    await run(async () => {
      const draft = await deliveryZonesApi.createVersion({
        name,
        source_version_id: sourceId,
      });
      setDraftName('');
      setOpenId(draft.id);
    });
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return <div className="text-sm text-red-500 font-body">{error}</div>;
  }

  const pending = batches.filter(b => b.status === 'pending').length;

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <h1 className="font-display text-xl text-gray-800">Delivery Zones</h1>
        <p className="text-xs text-gray-400 font-body mt-1">
          What each area costs to deliver to, who carries it, and when their orders
          travel together. One map is live at a time; to change a price, copy it to a
          draft and publish the draft.
        </p>
      </div>

      <TabBar
        tabs={[
          { key: 'map', label: 'Map' },
          { key: 'maps', label: 'Fees & couriers', count: versions.length },
          { key: 'batching', label: 'Batching' },
          { key: 'runs', label: 'Runs', count: pending || undefined },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === 'map' && zoneMap && (
        <div className="bg-white border border-gray-200 p-4 mb-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-3">
            {zoneMap.version?.name ?? 'No map published'}
          </p>
          <ZoneMap data={zoneMap} />
        </div>
      )}

      {tab === 'batching' && zoneMap && (
        <BatchingTab />
      )}

      {tab === 'runs' && (
        <RunsTab
          batches={batches}
          busy={busy}
          onDispatch={id =>
            confirm('Send this run to the courier now?') &&
            run(() => deliveryZonesApi.dispatchBatch(id))
          }
        />
      )}

      {tab === 'maps' && settings && (
        <SettingsCard
          settings={settings}
          busy={busy}
          onSave={data => run(() => deliveryZonesApi.updateSettings(data))}
        />
      )}

      <div className={cn('space-y-3', tab !== 'maps' && 'hidden')}>
        {versions.map(version => (
          <VersionCard
            key={version.id}
            version={version}
            branches={branches}
            open={openId === version.id}
            busy={busy}
            draftName={draftName}
            onToggle={() => setOpenId(openId === version.id ? null : version.id)}
            onDraftNameChange={setDraftName}
            onCopy={() => createDraft(version.id)}
            onPublish={() =>
              confirm(
                `Publish "${version.name}"? Every new order is priced from it immediately.`,
              ) && run(() => deliveryZonesApi.publish(version.id))
            }
            onDelete={() =>
              confirm(`Delete the draft "${version.name}"?`) &&
              run(() => deliveryZonesApi.deleteVersion(version.id))
            }
            onZoneChange={(zoneId, data) =>
              run(() => deliveryZonesApi.updateZone(zoneId, data))
            }
          />
        ))}
      </div>
    </div>
  );
}

interface VersionCardProps {
  version: DeliveryMapVersion;
  branches: Branch[];
  open: boolean;
  busy: boolean;
  draftName: string;
  onToggle: () => void;
  onDraftNameChange: (value: string) => void;
  onCopy: () => void;
  onPublish: () => void;
  onDelete: () => void;
  onZoneChange: (
    zoneId: string,
    data: {
      delivery_fee?: number;
      pricing_mode?: DeliveryPricingMode;
      free_delivery_eligible?: boolean;
      free_delivery_threshold?: number;
      fulfilment_provider?: FulfilmentProvider;
      branch_id?: string;
    },
  ) => void;
}

function VersionCard({
  version,
  branches,
  open,
  busy,
  draftName,
  onToggle,
  onDraftNameChange,
  onCopy,
  onPublish,
  onDelete,
  onZoneChange,
}: VersionCardProps) {
  return (
    <div
      className={cn(
        'bg-white border',
        version.is_active ? 'border-primary' : 'border-gray-200',
      )}
    >
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
      >
        <span className="material-icons text-[18px] text-gray-400">
          {open ? 'expand_less' : 'expand_more'}
        </span>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-body text-gray-800">{version.name}</span>
            {version.is_active ? (
              <Badge variant="success">Live</Badge>
            ) : (
              <Badge variant="neutral">Draft</Badge>
            )}
          </div>
          <p className="text-[11px] font-body text-gray-400 mt-0.5">
            {version.polygons.length} zones · created {formatDate(version.created_at)}
            {version.activated_at && ` · published ${formatDate(version.activated_at)}`}
          </p>
        </div>
      </button>

      {open && (
        <div className="border-t border-gray-100">
          {version.notes && (
            <p className="px-4 py-3 text-[11px] font-body text-gray-500 bg-gray-50 border-b border-gray-100">
              {version.notes}
            </p>
          )}

          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Zone
                </th>
                <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Priced by
                </th>
                <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Fee
                </th>
                <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Fulfilled by
                </th>
                <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Baked at
                </th>
                <th className="px-4 py-2 text-center text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Free over threshold
                </th>
                <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Order
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {version.polygons.map(zone => (
                <ZoneRow
                  // The saved values are part of the key so a reload after a
                  // save remounts the row with them, rather than leaving the
                  // input showing what was typed before the server answered.
                  key={`${zone.id}:${zone.delivery_fee}:${zone.pricing_mode}:${zone.fulfilment_provider}:${zone.free_delivery_eligible}:${zone.free_delivery_threshold}:${zone.branch_id}`}
                  zone={zone}
                  branches={branches}
                  readOnly={version.is_active || busy}
                  onChange={data => onZoneChange(zone.id, data)}
                />
              ))}
            </tbody>
          </table>

          <p className="px-4 pt-3 text-[11px] font-body text-gray-400">
            Zones are matched top to bottom, so a smaller area listed above the one it
            sits inside wins. A courier-priced zone charges whatever the courier quotes
            for the customer&rsquo;s pin, rounded up to the dirham — and refuses the order
            when there is no quote.
          </p>

          <div className="flex flex-wrap items-end gap-2 px-4 py-3">
            <Input
              label="Copy to a new draft"
              value={draftName}
              onChange={e => onDraftNameChange(e.target.value)}
              placeholder="e.g. Ramadan pricing"
              className="w-56"
            />
            <Button size="sm" variant="ghost" onClick={onCopy} disabled={busy}>
              <span className="material-icons text-[14px]">content_copy</span>
              Copy
            </Button>
            <div className="flex-1" />
            {!version.is_active && (
              <>
                <Button size="sm" onClick={onPublish} disabled={busy}>
                  <span className="material-icons text-[14px]">publish</span>
                  Publish
                </Button>
                <Button size="sm" variant="danger" onClick={onDelete} disabled={busy}>
                  <span className="material-icons text-[14px]">delete</span>
                  Delete
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ZoneRow({
  zone,
  branches,
  readOnly,
  onChange,
}: {
  zone: DeliveryZone;
  branches: Branch[];
  readOnly: boolean;
  onChange: (data: {
    delivery_fee?: number;
    pricing_mode?: DeliveryPricingMode;
    free_delivery_eligible?: boolean;
    free_delivery_threshold?: number;
    fulfilment_provider?: FulfilmentProvider;
    branch_id?: string;
  }) => void;
}) {
  const [fee, setFee] = useState(String(zone.delivery_fee));
  const [threshold, setThreshold] = useState(String(zone.free_delivery_threshold));
  const dynamic = zone.pricing_mode === 'dynamic';

  function commitThreshold() {
    const parsed = Number(threshold);
    // Zero is a real value here — Sharjah delivers free at any basket — so the
    // guard is on "not a number" and "negative", never on falsiness.
    if (
      !Number.isFinite(parsed) ||
      parsed < 0 ||
      parsed === zone.free_delivery_threshold
    ) {
      setThreshold(String(zone.free_delivery_threshold));
      return;
    }
    onChange({ free_delivery_threshold: parsed });
  }

  function commitFee() {
    const parsed = Number(fee);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed === zone.delivery_fee) {
      setFee(String(zone.delivery_fee));
      return;
    }
    onChange({ delivery_fee: parsed });
  }

  return (
    <tr>
      <td className="px-4 py-2.5">
        <div className="text-xs font-body text-gray-800">{zone.name}</div>
        <div className="text-[11px] font-body text-gray-400">
          {zone.point_count.toLocaleString()} points
        </div>
      </td>
      <td className="px-4 py-2.5">
        {readOnly ? (
          <Badge variant={dynamic ? 'warning' : 'neutral'}>
            {PRICING_LABEL[zone.pricing_mode]}
          </Badge>
        ) : (
          <Select
            value={zone.pricing_mode}
            options={PRICING_OPTIONS}
            onChange={e =>
              onChange({ pricing_mode: e.target.value as DeliveryPricingMode })
            }
            className="w-36"
          />
        )}
      </td>
      <td className="px-4 py-2.5 text-right">
        {/* A courier-priced zone has no fee of its own. Showing an editable
            number here would invite someone to set one and then wonder why no
            order ever charged it. */}
        {dynamic ? (
          <span className="text-xs font-body text-gray-400">Per pin</span>
        ) : readOnly ? (
          <span className="text-xs font-body text-gray-700">
            {formatCurrency(zone.delivery_fee)}
          </span>
        ) : (
          <input
            value={fee}
            onChange={e => setFee(e.target.value)}
            onBlur={commitFee}
            inputMode="decimal"
            className="w-20 px-2 py-1 text-xs font-body text-right bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30"
          />
        )}
      </td>
      <td className="px-4 py-2.5">
        {readOnly ? (
          <Badge variant={PROVIDER_BADGE[zone.fulfilment_provider] ?? 'neutral'}>
            {PROVIDER_LABEL[zone.fulfilment_provider] ?? zone.fulfilment_provider}
          </Badge>
        ) : (
          <Select
            value={zone.fulfilment_provider}
            options={PROVIDER_OPTIONS}
            onChange={e =>
              onChange({ fulfilment_provider: e.target.value as FulfilmentProvider })
            }
            className="w-36"
          />
        )}
      </td>
      <td className="px-4 py-2.5">
        {readOnly ? (
          <span className="text-xs font-body text-gray-700">
            {branches.find(b => b.id === zone.branch_id)?.reference ?? '—'}
          </span>
        ) : (
          <Select
            value={zone.branch_id ?? ''}
            options={branches.map(b => ({
              value: b.id,
              label: `${b.reference} · ${b.name}`,
            }))}
            onChange={e => onChange({ branch_id: e.target.value })}
            className="w-44"
          />
        )}
      </td>
      <td className="px-4 py-2.5 text-center">
        {/* Two settings, deliberately together: whether this zone makes the
            offer at all, and the basket that earns it. They were one badge
            reading "Free", which said neither — and the threshold, which is now
            per zone, could not be seen or changed here at all. */}
        {readOnly ? (
          <Badge variant={zone.free_delivery_eligible ? 'success' : 'neutral'}>
            {!zone.free_delivery_eligible
              ? 'Always charged'
              : zone.free_delivery_threshold > 0
                ? `Free over ${formatCurrency(zone.free_delivery_threshold)}`
                : 'Always free'}
          </Badge>
        ) : (
          <div className="flex items-center justify-center gap-2">
            <input
              type="checkbox"
              aria-label={`Free delivery over the threshold in ${zone.name}`}
              checked={zone.free_delivery_eligible}
              onChange={e => onChange({ free_delivery_eligible: e.target.checked })}
              className="w-4 h-4 accent-primary cursor-pointer"
            />
            <input
              type="number"
              min="0"
              step="0.01"
              aria-label={`Free delivery threshold for ${zone.name}`}
              title="The basket that earns free delivery here. 0 means free at any basket."
              value={threshold}
              disabled={!zone.free_delivery_eligible}
              onChange={e => setThreshold(e.target.value)}
              onBlur={commitThreshold}
              onKeyDown={e => e.key === 'Enter' && e.currentTarget.blur()}
              className="w-20 border border-gray-300 px-2 py-1 text-xs font-body text-right disabled:bg-gray-50 disabled:text-gray-400"
            />
          </div>
        )}
      </td>
      <td className="px-4 py-2.5 text-right text-xs font-body text-gray-400">
        {zone.display_order}
      </td>
    </tr>
  );
}

// ── Batching ──────────────────────────────────────────────────────────────────

/**
 * The schedule, per group.
 *
 * A group is a set of zones whose orders ride together on one courier booking.
 * This screen used to list zones, each with its own schedule, and which of them
 * actually shared a van fell out of two schedules coincidentally ending on the
 * same minute — a decision nobody made and this page could not show. Listing
 * groups is the point: what you see here is what leaves together.
 *
 * A zone in no group is not missing a schedule. It dispatches the moment the
 * order is ready, which is the right answer for noon Send and for every third
 * party, and it is stated under each group rather than left as an absence.
 */
function BatchingTab() {
  const [groups, setGroups] = useState<BatchGroup[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    deliveryZonesApi
      .listBatchGroups()
      .then(rows => {
        setGroups(rows);
        setOpen(rows[0]?.id ?? null);
      })
      .catch(err => setError((err as Error).message));
  }, []);

  if (error) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-red-600">
        {error}
      </div>
    );
  }
  if (groups === null) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-400">
        Loading schedules…
      </div>
    );
  }
  if (!groups.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        No batch groups. Every zone dispatches its orders the moment they are
        ready. Only a courier that can carry several of our orders in one
        booking can have a schedule at all.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {groups.map(group => (
        <div key={group.id} className="bg-white border border-gray-200">
          <button
            onClick={() => setOpen(open === group.id ? null : group.id)}
            className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
          >
            <span className="material-icons text-[18px] text-gray-400">
              {open === group.id ? 'expand_less' : 'expand_more'}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-body text-gray-800">
                {group.name}
              </span>
              {/* The zones on this van, spelled out. The whole reason the group
                  exists is that this list used to be unknowable. */}
              <span className="block text-xs font-body text-gray-400 truncate">
                {group.zone_names.join(' · ') || 'No zones on this schedule yet'}
              </span>
            </span>
            <span className="text-xs font-body text-gray-500 shrink-0">
              {PROVIDER_LABEL[group.courier_code as FulfilmentProvider] ??
                group.courier_code}{' '}
              ·{' '}
              {group.delivery_minutes_after_dispatch}m to the door
            </span>
          </button>
          {open === group.id && (
            <div className="border-t border-gray-100 px-4 py-3">
              <BatchWindows groupId={group.id} zoneName={group.name} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Runs ──────────────────────────────────────────────────────────────────────

const BATCH_VARIANT: Record<DeliveryBatch['status'], 'warning' | 'info' | 'success' | 'danger' | 'neutral'> = {
  pending: 'warning',
  dispatching: 'info',
  dispatched: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

/** What has gone out together, and what it saved. */
function RunsTab({
  batches,
  busy,
  onDispatch,
}: {
  batches: DeliveryBatch[];
  busy: boolean;
  onDispatch: (id: string) => void;
}) {
  if (!batches.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        No runs yet. One appears as soon as an order in a courier zone is packed
        inside a batch window.
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50">
            <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Run</th>
            <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Leaves</th>
            <th className="px-4 py-2 text-center text-[11px] font-body uppercase tracking-widest text-gray-400">Drops</th>
            <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Cost</th>
            <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Each</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {batches.map(batch => (
            <tr key={batch.id}>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-body text-gray-800">
                    {batch.zone_name ?? '—'} · {batch.window_label ?? 'run'}
                  </span>
                  <Badge variant={BATCH_VARIANT[batch.status]}>{batch.status}</Badge>
                </div>
                {batch.order_numbers.length > 0 && (
                  <div className="text-[11px] font-body text-gray-400 mt-0.5">
                    {batch.order_numbers.join(', ')}
                  </div>
                )}
                {batch.last_error && (
                  <div className="text-[11px] font-body text-red-600 mt-0.5">
                    {batch.last_error}
                  </div>
                )}
                {/* Whether this is being handled or is waiting on a person is
                    the only thing worth knowing about a failed run, and it is
                    not something a status badge can say on its own. */}
                {batch.next_attempt_at ? (
                  <div className="text-[11px] font-body text-gray-400 mt-0.5">
                    Attempt {batch.attempt_count} · trying again {formatDate(batch.next_attempt_at)}
                  </div>
                ) : batch.status === 'failed' && batch.attempt_count > 1 ? (
                  <div className="text-[11px] font-body text-gray-400 mt-0.5">
                    Gave up after {batch.attempt_count} attempts
                  </div>
                ) : null}
              </td>
              <td className="px-4 py-2.5 text-xs font-body text-gray-600">
                {formatDate(batch.dispatch_at)}
              </td>
              <td className="px-4 py-2.5 text-center text-xs font-body text-gray-600">
                {batch.stop_count}
              </td>
              <td className="px-4 py-2.5 text-right text-xs font-body text-gray-600">
                {batch.cost_total !== null ? formatCurrency(batch.cost_total) : '—'}
              </td>
              <td className="px-4 py-2.5 text-right text-xs font-body text-gray-800">
                {batch.cost_per_delivery !== null
                  ? formatCurrency(batch.cost_per_delivery)
                  : '—'}
              </td>
              <td className="px-4 py-2.5 text-right whitespace-nowrap">
                {batch.share_link && (
                  <a
                    href={batch.share_link}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] font-body text-primary hover:underline mr-3"
                  >
                    Track
                  </a>
                )}
                {/* A dispatched run with a retry pending is one whose second
                    courier order failed — part of it is on the road and part of
                    it is still in the kitchen, so the button has to stay. */}
                {batch.status !== 'cancelled' &&
                  (batch.status !== 'dispatched' || batch.next_attempt_at) && (
                    <button
                      onClick={() => onDispatch(batch.id)}
                      disabled={busy}
                      className="text-[11px] font-body text-primary hover:underline"
                    >
                      {batch.status === 'pending' ? 'Send now' : 'Retry'}
                    </button>
                  )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Settings ──────────────────────────────────────────────────────────────────

/**
 * The three delivery numbers that belong to no zone.
 *
 * They used to live on a Regions screen alongside a table of emirates and
 * their fees. The emirates are gone — the pin decides the price — and these
 * three were the only part of that screen still worth keeping.
 */
function SettingsCard({
  settings,
  busy,
  onSave,
}: {
  settings: DeliverySettings;
  busy: boolean;
  onSave: (data: { pickup_fee?: number }) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({
    pickup_fee: String(settings.pickup_fee),
    low_order_fee: String(settings.low_order_fee ?? 0),
    // Null renders as blank, which is what the field means: fee switched off.
    low_order_threshold:
      settings.low_order_threshold === null
        ? ''
        : String(settings.low_order_threshold),
  });

  // "Free delivery above" and "Outside every zone" used to live here and are
  // gone. The first is per zone now — a bike run inside Sharjah and a car to
  // Jebel Ali cannot share one threshold — and it is edited in the zone table
  // below. The second described a fee for a pin outside every shape, and the
  // map tiles the whole country, so such a pin is outside the UAE and is
  // refused rather than priced.
  const FIELDS = [
    { key: 'pickup_fee' as const, label: 'Pickup', hint: 'Usually nothing.' },
    {
      key: 'low_order_fee' as const,
      label: 'Small order fee',
      hint: 'Charged on delivery orders at or below the basket size beside it. Never on pickup.',
    },
    {
      key: 'low_order_threshold' as const,
      label: 'Small order below',
      hint: 'Inclusive — a basket of exactly this much still pays. Leave blank to switch the fee off.',
      // The only field here that may be empty, and empty means something: no
      // threshold is how the fee is turned off. Zero would charge every basket.
      nullable: true,
    },
  ];

  function save() {
    const parsed: Record<string, number | null> = {};
    for (const field of FIELDS) {
      const raw = String(form[field.key] ?? '').trim();
      // A blank nullable field is an instruction, not a missing value: it
      // switches the small-order fee off. Coercing it to 0 would instead charge
      // the fee on every basket, which is the opposite.
      if (!raw && 'nullable' in field && field.nullable) {
        parsed[field.key] = null;
        continue;
      }
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0) {
        alert('Every amount has to be a number, and none of them can be negative.');
        return;
      }
      parsed[field.key] = value;
    }
    onSave(parsed as Parameters<typeof onSave>[0]);
    setEditing(false);
  }

  return (
    <div className="bg-white border border-gray-200 p-4 mb-4">
      <div className="flex items-center mb-3">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 flex-1">
          Applies to every zone
        </p>
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            className="text-[11px] font-body text-primary hover:underline"
          >
            Edit
          </button>
        )}
      </div>

      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs font-body">
        {FIELDS.map(field => (
          <div key={field.key}>
            <dt className="text-gray-500">{field.label}</dt>
            {editing ? (
              <input
                value={form[field.key]}
                onChange={e => setForm({ ...form, [field.key]: e.target.value })}
                inputMode="decimal"
                className="mt-1 w-24 px-2 py-1 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
              />
            ) : (
              <dd className="text-gray-800 text-sm">
                {Number(settings[field.key]) > 0
                  ? formatCurrency(Number(settings[field.key]))
                  : 'Free'}
              </dd>
            )}
            <p className="text-[10px] text-gray-400 mt-1">{field.hint}</p>
          </div>
        ))}
      </dl>

      {editing && (
        <div className="flex justify-end gap-2 mt-3">
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={busy}>
            Save
          </Button>
        </div>
      )}
    </div>
  );
}
