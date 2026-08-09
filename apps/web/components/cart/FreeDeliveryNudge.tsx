'use client';

import { useEffect, useRef } from 'react';
import { analytics } from '@/lib/analytics';
import { useLocation } from '@/lib/location/LocationProvider';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { Icon } from '@/components/ui/Icon';

interface FreeDeliveryNudgeProps {
  /** The basket the threshold is measured against, after any discount. */
  total: number;
}

/** 150 rather than "150.00"; 12.5 keeps its half. */
function amount(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/**
 * How much more this basket needs to earn free delivery here.
 *
 * The checkout already says this. It says it too late — by then the basket is
 * decided and the sentence only informs somebody that they are paying for
 * delivery. The cart is the screen where a second box is still a live option,
 * which is the whole reason this exists.
 *
 * **The rule it inherits from `DeliveryPromiseBanner`, and the reason for it:**
 * a free-delivery claim is only ever made about a zone we can name. There is no
 * national threshold that is true — Sharjah is free at any basket and the far
 * emirates want 200 — so a generic "spend 150 more" is wrong almost everywhere
 * it would be read. No serviceable area, or no zone name, means this renders
 * nothing at all. Silence is the honest answer, and a promise nobody can keep
 * costs more than the order it was meant to grow.
 *
 * Pickup baskets are not this component's problem: it only ever appears on the
 * cart, where no handover method has been chosen yet, and the checkout takes
 * over the moment one is.
 */
export function FreeDeliveryNudge({ total }: FreeDeliveryNudgeProps) {
  const { t } = useTranslation();
  const { area } = useLocation();

  const zone = area?.zone_name ?? null;
  const threshold = area?.free_threshold ?? null;

  const qualified =
    Boolean(area?.serviceable && area?.free_delivery_available && zone) &&
    threshold !== null &&
    total >= threshold;

  // Once per crossing, not once per render. `total` changes on every quantity
  // tap, and an event per tap would make one unlocked basket look like six.
  const announced = useRef(false);
  useEffect(() => {
    if (qualified && !announced.current) {
      announced.current = true;
      analytics.freeDeliveryUnlocked({
        threshold: threshold ?? 0,
        subtotal: total,
        surface: 'cart',
        zone: zone ?? undefined,
      });
    }
    if (!qualified) announced.current = false;
  }, [qualified, zone, threshold, total]);

  // Nothing we can bound to a place, so nothing said. See the note above.
  if (!area || !area.serviceable || !area.free_delivery_available || !zone) return null;
  if (threshold === null) return null;

  // Free here at any basket. There is no progress to make, and a full bar
  // implying the customer earned something by spending is a small lie.
  if (threshold === 0) {
    return (
      <p className="flex items-center gap-2 font-body text-xs text-green-700">
        <Icon name="local_shipping" className="text-base" />
        {t('cart.free_delivery_here', { area: zone })}
      </p>
    );
  }

  if (qualified) {
    return (
      <p className="flex items-center gap-2 font-body text-xs text-green-700">
        <Icon name="check_circle" className="text-base" />
        {t('cart.free_delivery_qualified', { area: zone })}
      </p>
    );
  }

  const remaining = threshold - total;
  const progress = Math.min(100, Math.max(0, (total / threshold) * 100));

  return (
    <div className="space-y-1.5">
      <p className="font-body text-xs text-gray-600">
        {t('cart.free_delivery_remaining', { amount: amount(remaining), area: zone })}
      </p>
      {/*
        `dir="ltr"` pins the fill to the start edge in Arabic too: the bar is a
        quantity, and a quantity that grows right-to-left reads as shrinking.
      */}
      <div
        dir="ltr"
        className="h-1 w-full rounded-full bg-gray-100 overflow-hidden"
        role="progressbar"
        aria-valuenow={Math.round(progress)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t('cart.free_delivery_progress_label')}
      >
        <div
          className="h-full bg-primary transition-[width] duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}
