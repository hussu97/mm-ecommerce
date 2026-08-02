'use client';

import { useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import type {
  DeliveryMapVersion,
  DeliveryZone,
  DeliveryZoneSummary,
  FulfilmentProvider,
} from '@/lib/types';
import { Badge, Button, Input, Select, Spinner } from '@/components/ui';
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

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [v, s] = await Promise.all([
        deliveryZonesApi.listVersions(),
        deliveryZonesApi.summary(),
      ]);
      setVersions(v);
      setSummary(s);
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

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <h1 className="font-display text-xl text-gray-800">Delivery Zones</h1>
        <p className="text-xs text-gray-400 font-body mt-1">
          What each area costs to deliver to, and who carries it. One map is live at a
          time; to change a price, copy it to a draft and publish the draft.
        </p>
      </div>

      {summary && (
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

      <div className="space-y-3">
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
