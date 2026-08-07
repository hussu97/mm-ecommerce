'use client';

import { useLocation } from '@/lib/location/LocationProvider';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { withFallback } from '@/lib/i18n/fallback';

/**
 * How fast this reaches the customer, where the customer actually is.
 *
 * The same component on a product card, a product page and a cart line, because
 * three different answers to "when does this arrive" on three screens of one
 * journey is worse than no answer at all.
 *
 * It reads the location context rather than taking props, so every instance
 * moves together the moment the customer changes their address — and none of
 * them can be left rendering a promise about somewhere else.
 */

const SPEED_KEYS = {
  express: ['usp.speed_express', 'Delivered in about an hour'],
  same_day: ['usp.speed_same_day', 'Same-day delivery'],
  next_day: ['usp.speed_next_day', 'Next-day delivery'],
} as const;

export function DeliveryEstimate({
  /** `card` is the compact one-liner; `detail` is the PDP treatment. */
  variant = 'card',
  className = '',
}: {
  variant?: 'card' | 'detail';
  className?: string;
}) {
  const { area, loading, isKnown } = useLocation();
  const { t } = useTranslation();

  // Nothing honest to say yet. Rendering a placeholder that later changes into
  // a different promise is worse than rendering nothing: the customer reads the
  // first one.
  if (loading || !area) return null;

  const [key, english] = SPEED_KEYS[area.speed] ?? SPEED_KEYS.next_day;
  const speed = withFallback(t, key, english);

  const detail = variant === 'detail';

  return (
    <p
      className={[
        'flex items-center gap-1.5 font-body',
        detail ? 'text-sm text-gray-700' : 'text-[11px] text-gray-600',
        className,
      ].join(' ')}
      // The speed is the claim; the place is context. Announced together so a
      // screen reader gets the qualified version rather than a bare promise.
      aria-label={
        area.zone_name ? `${speed} — ${area.zone_name}` : speed
      }
    >
      <span
        aria-hidden="true"
        className={[
          'material-symbols-outlined leading-none text-primary',
          detail ? 'text-[18px]' : 'text-[14px]',
        ].join(' ')}
      >
        local_shipping
      </span>
      <span className="font-semibold text-gray-900">{speed}</span>
      {/* Only named when we actually know it. An assumed area printed as fact
          is the failure this whole context exists to avoid. */}
      {isKnown && area.zone_name ? (
        <span className="text-gray-500">· {area.zone_name}</span>
      ) : null}
    </p>
  );
}
