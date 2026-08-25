'use client';

import { useLocation } from '@/lib/location/LocationProvider';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { speedLabel } from '@/lib/location/speed';

/**
 * How fast this reaches the customer — as a claim, not a hedge.
 *
 * The same component on a product card, a product page and a cart line, because
 * three different answers to "when does this arrive" on three screens of one
 * journey is worse than no answer at all. It reads the location context rather
 * than taking props, so every instance moves together the moment the customer
 * changes their address.
 *
 * Two things it deliberately does not do. It does not name the place: the
 * customer knows where they live, and "· Sharjah" repeated down a grid of nine
 * cards is nine copies of something nobody asked. And it does not hedge — "in
 * about an hour" is the same promise as "in 60 minutes" said apologetically,
 * and this is the strongest thing the shop can say about itself. It is set to
 * look like a badge for that reason, not like a caption.
 */

/**
 * A van with a speed line through it, drawn rather than named.
 *
 * This was a `material-symbols-outlined` ligature, and the site only loads
 * Material *Icons* — so the class matched no font and the word `local_shipping`
 * rendered as literal text on every product card in the catalogue. An inline
 * SVG cannot fail that way: it has no font to wait for, no network request of
 * its own, and it inherits `currentColor` so it stays right in both the badge
 * and anywhere else it lands.
 */
function SpeedIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {/* the box */}
      <path d="M9 17h6" />
      <path d="M9 7.5h5.5a1 1 0 0 1 1 1V17" />
      {/* the cab */}
      <path d="M15.5 11h2.6a1 1 0 0 1 .84.46l1.4 2.2a1 1 0 0 1 .16.54V17h-1.5" />
      {/* wheels */}
      <circle cx="7.5" cy="17" r="1.6" />
      <circle cx="17.5" cy="17" r="1.6" />
      {/* the speed lines — the whole point of the mark */}
      <path d="M2.5 9.5h4" />
      <path d="M1 12.5h3.5" />
      <path d="M3 15.5h2.5" />
    </svg>
  );
}

/**
 * The pill itself, so every surface that shows a delivery time looks the same.
 *
 * Exported because four screens need it and they do not all say the same thing:
 * a product card names a speed ("Get it in 60 minutes") because it has no
 * address, while the checkout names a date and time because it has a pin and a
 * real schedule behind it. Same badge, different sentence — which is the point.
 * A shared component rather than a copied class list, or the four drift apart
 * one tweak at a time.
 */
export function SpeedBadge({
  children,
  size = 'card',
  className = '',
}: {
  children: React.ReactNode;
  size?: 'card' | 'detail';
  className?: string;
}) {
  const detail = size === 'detail';
  return (
    <p
      className={[
        'inline-flex items-center gap-1.5 rounded-full font-body font-semibold',
        // Translucent rather than solid: it has to read as a badge on a white
        // card and on a photograph, and a flat fill only works on one of them.
        'bg-primary/10 text-primary ring-1 ring-inset ring-primary/15',
        detail ? 'text-sm px-3 py-1.5' : 'text-[12px] px-2.5 py-1',
        className,
      ].join(' ')}
    >
      <SpeedIcon className={detail ? 'h-[18px] w-[18px]' : 'h-[15px] w-[15px]'} />
      {children}
    </p>
  );
}

export function DeliveryEstimate({
  /** `card` is the compact badge; `detail` is the larger PDP treatment. */
  variant = 'card',
  className = '',
}: {
  variant?: 'card' | 'detail';
  className?: string;
}) {
  const { area, loading } = useLocation();
  const { t } = useTranslation();

  // Nothing honest to say yet. Rendering a placeholder that later changes into a
  // different promise is worse than rendering nothing: the customer reads the
  // first one.
  if (loading || !area) return null;

  return (
    <SpeedBadge size={variant} className={className}>
      {speedLabel(t, area)}
    </SpeedBadge>
  );
}
