'use client';

import { Badge, Button, Input } from '@/components/ui';
import { cn, formatDate } from '@/lib/utils';
import { ZoneRow } from './ZoneRow';
import type { Branch } from '@/lib/pos-types';
import type { DeliveryMapVersion, DeliveryPricingMode, FulfilmentProvider } from '@/lib/types';

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
      alternate_providers?: FulfilmentProvider[];
      branch_id?: string;
    },
  ) => void;
}


export function VersionCard({
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
