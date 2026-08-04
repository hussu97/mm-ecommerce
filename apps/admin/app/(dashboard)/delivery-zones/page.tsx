'use client';

import { useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type {
  DeliveryBatch,
  DeliveryMapVersion,
  DeliveryZone,
  DeliveryZoneMap,
  DeliveryZoneShape,
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
                <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Fee
                </th>
                <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Fulfilled by
                </th>
                <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
                  Baked at
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
                  key={`${zone.id}:${zone.delivery_fee}:${zone.fulfilment_provider}:${zone.branch_id}`}
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
  branches,
  readOnly,
  onChange,
}: {
  zone: DeliveryZone;
  branches: Branch[];
  readOnly: boolean;
  onChange: (data: {
    delivery_fee?: number;
    fulfilment_provider?: FulfilmentProvider;
    branch_id?: string;
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
          {zone.point_count.toLocaleString()} points
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
 * Only Lalamove zones appear. A third-party zone has no run of ours for its
 * orders to share, and noon Send's shared run is a different product with its
 * own endpoint and a cap of three — so a schedule on either would be a setting
 * that does nothing, which is worse than an absent one because somebody will
 * eventually rely on it.
 */
function BatchingTab({ zones }: { zones: DeliveryZoneShape[] }) {
  const courierZones = zones.filter(z => z.fulfilment_provider === 'lalamove');
  const [openZone, setOpenZone] = useState<string | null>(courierZones[0]?.id ?? null);

  if (!courierZones.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        No zone on the live map is delivered by Lalamove, so there is nothing to
        batch. Only Lalamove runs can carry several orders at once — set a zone
        to “Lalamove” under Fees &amp; couriers first.
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
  onSave: (data: {
    free_delivery_threshold?: number;
    pickup_fee?: number;
    default_delivery_fee?: number;
  }) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    free_delivery_threshold: String(settings.free_delivery_threshold),
    pickup_fee: String(settings.pickup_fee),
    default_delivery_fee: String(settings.default_delivery_fee),
  });

  const FIELDS = [
    {
      key: 'free_delivery_threshold' as const,
      label: 'Free delivery above',
      hint: 'The same in every zone — the one delivery number a customer sees.',
    },
    {
      key: 'default_delivery_fee' as const,
      label: 'Outside every zone',
      hint: 'A real address we have not drawn a shape around yet.',
    },
    { key: 'pickup_fee' as const, label: 'Pickup', hint: 'Usually nothing.' },
  ];

  function save() {
    const parsed = Object.fromEntries(
      FIELDS.map(f => [f.key, Number(form[f.key])]),
    ) as Record<(typeof FIELDS)[number]['key'], number>;
    if (Object.values(parsed).some(v => !Number.isFinite(v) || v < 0)) {
      alert('Every amount has to be a number, and none of them can be negative.');
      return;
    }
    onSave(parsed);
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
