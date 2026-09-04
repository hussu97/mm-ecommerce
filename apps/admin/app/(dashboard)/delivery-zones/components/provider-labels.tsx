'use client';

/**
 * The courier vocabulary the delivery-map screens share, and the two small
 * components that render an alternate-provider choice.
 *
 * Named "provider" rather than "Courier API": there are five fulfilment types
 * now — Lalamove, noon Send, the two Slider fleets (bike and car) and a third
 * party — they cost different amounts, so which is which matters. Bare `slider`
 * lingers as the legacy value the two fleets split out of, kept last so a new
 * map is nudged towards the fleet it actually means.
 */

import type { DeliveryPricingMode, FulfilmentProvider } from '@/lib/types';
import { cn } from '@/lib/utils';

// Named rather than called "Courier API": there are five fulfilment types now,
// they cost different amounts — so which is which matters. `slider` is the
// legacy single fleet the bike/car pair replaced.
export const PROVIDER_LABEL: Record<FulfilmentProvider, string> = {
  lalamove: 'Lalamove',
  noon_send: 'noon Send',
  slider_bike: 'Slider (bike)',
  slider_car: 'Slider (car)',
  slider: 'Slider',
  third_party: 'Third party',
};

export const PROVIDER_OPTIONS = [
  { value: 'lalamove', label: PROVIDER_LABEL.lalamove },
  { value: 'noon_send', label: PROVIDER_LABEL.noon_send },
  { value: 'slider_bike', label: PROVIDER_LABEL.slider_bike },
  { value: 'slider_car', label: PROVIDER_LABEL.slider_car },
  { value: 'slider', label: PROVIDER_LABEL.slider },
  { value: 'third_party', label: PROVIDER_LABEL.third_party },
];

/**
 * Where a zone's orders may be moved when its own courier will not carry them.
 *
 * Mirrors `DEFAULT_ALTERNATES` in the API, and applied here only when somebody
 * changes a zone's preferred courier — the stored value is what the server
 * actually reads. Deliberately not "every other courier": noon Send cannot
 * cross an emirate boundary or carry a run past 20 km, so a Lalamove zone is
 * offered a third party and not them.
 */
export const DEFAULT_ALTERNATES: Record<FulfilmentProvider, FulfilmentProvider[]> = {
  lalamove: ['third_party'],
  third_party: ['lalamove'],
  noon_send: ['third_party', 'lalamove'],
  // A bike zone falls back to the car first — same fleet, no new integration —
  // then to Lalamove and a third party as the manual escape.
  slider_bike: ['slider_car', 'lalamove', 'third_party'],
  // A car zone has no smaller Slider to drop to, so it goes straight to
  // Lalamove and a third party.
  slider_car: ['lalamove', 'third_party'],
  // Legacy single-fleet Slider is offered Lalamove, which has none of noon
  // Send's limits, and a third party as the manual escape. Not noon Send by
  // default: most Slider zones are outside Sharjah, where noon Send cannot go.
  slider: ['lalamove', 'third_party'],
};

export const ALL_PROVIDERS: FulfilmentProvider[] = [
  'lalamove',
  'noon_send',
  'slider_bike',
  'slider_car',
  'slider',
  'third_party',
];

/** The alternates of a zone nobody is editing, as a sentence rather than controls. */
export function AlternateSummary({ zone }: { zone: { alternate_providers?: FulfilmentProvider[] } }) {
  const alternates = zone.alternate_providers ?? [];
  if (alternates.length === 0) {
    return (
      <span className="text-[11px] font-body text-gray-400">
        no alternates — orders here cannot be moved
      </span>
    );
  }
  return (
    <span className="text-[11px] font-body text-gray-500">
      or {alternates.map(p => PROVIDER_LABEL[p] ?? p).join(', ')}
    </span>
  );
}

/**
 * Which couriers this zone's orders may be moved to.
 *
 * Checkboxes rather than a multi-select: there are four couriers, one of them
 * is always disabled, and the whole control is smaller than the dropdown it
 * would otherwise be. The preferred courier is shown greyed rather than hidden,
 * so the list reads as "these four, and this one is already carrying it"
 * instead of silently having three rows in one zone and four in another.
 */
export function AlternatePicker({
  preferred,
  chosen,
  onChange,
}: {
  preferred: FulfilmentProvider;
  chosen: FulfilmentProvider[];
  onChange: (next: FulfilmentProvider[]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-x-2 gap-y-0.5">
      {ALL_PROVIDERS.map(provider => {
        const isPreferred = provider === preferred;
        const ticked = chosen.includes(provider);
        return (
          <label
            key={provider}
            title={
              isPreferred
                ? 'Already carries this zone, so it cannot also be an alternate'
                : `Allow orders here to be moved to ${PROVIDER_LABEL[provider] ?? provider}`
            }
            className={cn(
              'flex items-center gap-1 text-[11px] font-body',
              isPreferred ? 'text-gray-300' : 'text-gray-600 cursor-pointer',
            )}
          >
            <input
              type="checkbox"
              disabled={isPreferred}
              checked={ticked && !isPreferred}
              onChange={() =>
                onChange(
                  ticked
                    ? chosen.filter(p => p !== provider)
                    // Rebuilt in a fixed order rather than appended, so two
                    // zones with the same alternates always read the same way.
                    : ALL_PROVIDERS.filter(p => p === provider || chosen.includes(p)),
                )
              }
              className="h-3 w-3 accent-primary"
            />
            {PROVIDER_LABEL[provider] ?? provider}
          </label>
        );
      })}
    </div>
  );
}

export const PROVIDER_BADGE: Record<FulfilmentProvider, 'info' | 'success' | 'warning' | 'neutral'> = {
  lalamove: 'info',
  noon_send: 'success',
  // The whole Slider family shares the one amber badge — the fleet is read from
  // the label, not the colour, and three amber variants would only muddy it.
  slider_bike: 'warning',
  slider_car: 'warning',
  slider: 'warning',
  third_party: 'neutral',
};

export const PRICING_LABEL: Record<DeliveryPricingMode, string> = {
  static: 'Fixed fee',
  dynamic: 'Courier price',
};

export const PRICING_OPTIONS = [
  { value: 'static', label: PRICING_LABEL.static },
  { value: 'dynamic', label: PRICING_LABEL.dynamic },
];
