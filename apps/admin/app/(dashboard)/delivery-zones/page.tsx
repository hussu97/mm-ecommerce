'use client';

import { useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import type {
  DeliveryBatch,
  DeliveryMapVersion,
  DeliveryZone,
  DeliveryZoneMap,
  DeliveryZoneShape,
  DeliveryZoneSummary,
  FulfilmentProvider,
} from '@/lib/types';
import { Badge, Button, Input, Select, Spinner, TabBar } from '@/components/ui';
import { BatchWindows } from '@/components/delivery/BatchWindows';
import { ZoneMap } from '@/components/delivery/ZoneMap';
import { cn, formatCurrency, formatDate } from '@/lib/utils';

const PROVIDER_LABEL: Record<FulfilmentProvider, string> = {
  lalamove: 'Courier API',
  third_party: 'Third party',
};

const PROVIDER_OPTIONS = [
  { value: 'lalamove', label: PROVIDER_LABEL.lalamove },
  { value: 'third_party', label: PROVIDER_LABEL.third_party },
];

export default function DeliveryZonesPage() {
  const [versions, setVersions] = useState<DeliveryMapVersion[]>([]);
  const [summary, setSummary] = useState<DeliveryZoneSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState('');
  const [tab, setTab] = useState('map');
  const [zoneMap, setZoneMap] = useState<DeliveryZoneMap | null>(null);
  const [batches, setBatches] = useState<DeliveryBatch[]>([]);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [v, s, m, b] = await Promise.all([
        deliveryZonesApi.listVersions(),
        deliveryZonesApi.summary(),
        deliveryZonesApi.map(),
        deliveryZonesApi.listBatches({ limit: 50 }),
      ]);
      setVersions(v);
      setSummary(s);
      setZoneMap(m);
      setBatches(b);
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
        <BatchingTab zones={zoneMap.zones} />
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

      {tab === 'maps' && summary && (
        <div className="bg-white border border-gray-200 p-4 mb-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-3">
            Applies to every zone
          </p>
          <dl className="grid grid-cols-3 gap-4 text-xs font-body">
            <div>
              <dt className="text-gray-500">Free delivery above</dt>
              <dd className="text-gray-800 text-sm">
                {formatCurrency(summary.free_threshold)}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">Outside every zone</dt>
              <dd className="text-gray-800 text-sm">
                {formatCurrency(summary.default_delivery_fee)}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">Pickup</dt>
              <dd className="text-gray-800 text-sm">
                {summary.pickup_fee > 0 ? formatCurrency(summary.pickup_fee) : 'Free'}
              </dd>
            </div>
          </dl>
        </div>
      )}

      <div className={cn('space-y-3', tab !== 'maps' && 'hidden')}>
        {versions.map(version => (
          <VersionCard
            key={version.id}
            version={version}
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
    data: { delivery_fee?: number; fulfilment_provider?: FulfilmentProvider },
  ) => void;
}

function VersionCard({
  version,
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
                <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Fee
                </th>
                <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Fulfilled by
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
                  key={`${zone.id}:${zone.delivery_fee}:${zone.fulfilment_provider}`}
                  zone={zone}
                  readOnly={version.is_active || busy}
                  onChange={data => onZoneChange(zone.id, data)}
                />
              ))}
            </tbody>
          </table>

          <p className="px-4 pt-3 text-[11px] font-body text-gray-400">
            Zones are matched top to bottom, so a smaller area listed above the one it
            sits inside wins.
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
  readOnly,
  onChange,
}: {
  zone: DeliveryZone;
  readOnly: boolean;
  onChange: (data: {
    delivery_fee?: number;
    fulfilment_provider?: FulfilmentProvider;
  }) => void;
}) {
  const [fee, setFee] = useState(String(zone.delivery_fee));

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
          {zone.region_slug ?? '—'} · {zone.point_count.toLocaleString()} points
        </div>
      </td>
      <td className="px-4 py-2.5 text-right">
        {readOnly ? (
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
          <Badge variant={zone.fulfilment_provider === 'lalamove' ? 'info' : 'neutral'}>
            {PROVIDER_LABEL[zone.fulfilment_provider]}
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
      <td className="px-4 py-2.5 text-right text-xs font-body text-gray-400">
        {zone.display_order}
      </td>
    </tr>
  );
}

// ── Batching ──────────────────────────────────────────────────────────────────

/**
 * The schedule, per zone.
 *
 * Only courier zones appear. A third-party zone has no run of ours for its
 * orders to share, so a schedule on it would be a setting that does nothing —
 * and a setting that does nothing is worse than an absent one, because
 * somebody will eventually rely on it.
 */
function BatchingTab({ zones }: { zones: DeliveryZoneShape[] }) {
  const courierZones = zones.filter(z => z.fulfilment_provider === 'lalamove');
  const [openZone, setOpenZone] = useState<string | null>(courierZones[0]?.id ?? null);

  if (!courierZones.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        No zone on the live map is delivered over the courier API, so there is
        nothing to batch. Set a zone to “Courier API” under Fees &amp; couriers first.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {courierZones.map(zone => (
        <div key={zone.id} className="bg-white border border-gray-200">
          <button
            onClick={() => setOpenZone(openZone === zone.id ? null : zone.id)}
            className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
          >
            <span className="material-icons text-[18px] text-gray-400">
              {openZone === zone.id ? 'expand_less' : 'expand_more'}
            </span>
            <span className="text-sm font-body text-gray-800 flex-1">{zone.name}</span>
            <span className="text-xs font-body text-gray-400">
              {formatCurrency(zone.delivery_fee)}
            </span>
          </button>
          {openZone === zone.id && (
            <div className="border-t border-gray-100 px-4 py-3">
              <BatchWindows zoneId={zone.id} zoneName={zone.name} />
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
                {batch.status !== 'dispatched' && batch.status !== 'cancelled' && (
                  <button
                    onClick={() => onDispatch(batch.id)}
                    disabled={busy}
                    className="text-[11px] font-body text-primary hover:underline"
                  >
                    {batch.status === 'failed' ? 'Retry' : 'Send now'}
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
