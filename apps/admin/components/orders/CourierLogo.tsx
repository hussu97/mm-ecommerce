import type { CourierBadge } from '@/lib/types';
import { courierLogo } from '@/lib/couriers';

/**
 * The carrier's logo, at a glance — a marketplace (Talabat, Noon Food…) or a
 * dispatched courier (Lalamove, noon Send…). The image URL comes from the API's
 * DB-backed `couriers` table, so a logo can be swapped in the database without
 * shipping the admin. Renders nothing when there is no carrier (a counter sale).
 */
export function CourierLogo({
  courier,
  size = 24,
  showName = false,
  className = '',
}: {
  courier?: CourierBadge | null;
  size?: number;
  showName?: boolean;
  className?: string;
}) {
  if (!courier) return null;
  return (
    <span
      className={`inline-flex items-center gap-2 ${className}`}
      title={courier.name}
    >
      {/* Plain img (not next/image): the logo is small and decorative, and this
          matches the content editors' pattern without a layout wrapper. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={courier.logo_url}
        alt={courier.name}
        width={size}
        height={size}
        style={{ width: size, height: size }}
        className="shrink-0 rounded-md border border-gray-200 bg-white object-contain"
      />
      {showName && <span className="text-sm text-gray-700">{courier.name}</span>}
    </span>
  );
}

/**
 * A carrier mark by code — the logo for a courier, a POS icon for the counter.
 * Used by the dashboard scorecards and the orders-list filter chips, where all
 * that is known is the code (and, on the dashboard, an authoritative logo URL).
 */
export function CourierMark({
  code,
  logoUrl,
  size = 16,
}: {
  code: string;
  logoUrl?: string | null;
  size?: number;
}) {
  if (code === 'counter') {
    return (
      <span
        className="material-icons text-gray-400"
        style={{ fontSize: size + 1 }}
        aria-hidden
      >
        point_of_sale
      </span>
    );
  }
  const src = logoUrl || courierLogo(code) || undefined;
  if (!src) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      style={{ width: size, height: size }}
      className="shrink-0 object-contain"
    />
  );
}
