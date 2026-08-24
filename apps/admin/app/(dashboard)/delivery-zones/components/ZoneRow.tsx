'use client';

import { useState } from 'react';
import { Badge, Select } from '@/components/ui';
import { formatCurrency } from '@/lib/utils';
import { PROVIDER_LABEL, PROVIDER_OPTIONS, DEFAULT_ALTERNATES, PROVIDER_BADGE, PRICING_LABEL, PRICING_OPTIONS, AlternateSummary, AlternatePicker } from './provider-labels';
import type { Branch } from '@/lib/pos-types';
import type { DeliveryPricingMode, DeliveryZone, FulfilmentProvider } from '@/lib/types';


export function ZoneRow({
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
    alternate_providers?: FulfilmentProvider[];
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
          <div className="flex flex-col gap-1">
            <Badge variant={PROVIDER_BADGE[zone.fulfilment_provider] ?? 'neutral'}>
              {PROVIDER_LABEL[zone.fulfilment_provider] ?? zone.fulfilment_provider}
            </Badge>
            <AlternateSummary zone={zone} />
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <Select
              value={zone.fulfilment_provider}
              options={PROVIDER_OPTIONS}
              onChange={e => {
                // Changing the preferred courier resets the alternates rather
                // than keeping them. Half of them are usually wrong by then —
                // the outgoing courier is now a perfectly good alternate and the
                // incoming one cannot be its own — and a list that is quietly
                // half-wrong is worse than one that is obviously fresh.
                const preferred = e.target.value as FulfilmentProvider;
                onChange({
                  fulfilment_provider: preferred,
                  alternate_providers: DEFAULT_ALTERNATES[preferred] ?? [],
                });
              }}
              className="w-36"
            />
            <AlternatePicker
              preferred={zone.fulfilment_provider}
              chosen={zone.alternate_providers ?? []}
              onChange={alternate_providers => onChange({ alternate_providers })}
            />
            {/* Changing the courier above detaches this zone from its run — a
                run is one booking with one courier — and the API does it rather
                than refusing, because nothing here can attach one back. So the
                run has to be visible while somebody is editing the field that
                drops it. */}
            {zone.batch_group_id && (
              <span className="text-[11px] font-body text-gray-500">
                on a shared run — changing the courier takes it off
              </span>
            )}
          </div>
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
