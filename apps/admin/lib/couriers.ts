/**
 * The carriers the shop delivers through, as one list the filters draw from.
 *
 * Mirrors the API's `courier_catalog` (and adds the synthetic `counter`), so the
 * orders-list courier filter offers the full set even for a carrier with no
 * orders in the current window — the dashboard's live `by_courier` only lists
 * carriers that have some. Logos follow the same convention the API uses
 * (`{LOGO_BASE}/{code}.png`); the dashboard scorecards use the API's own
 * `logo_url`, which is authoritative and swappable in the database.
 */

export type CourierGroup = 'counter' | 'website' | 'aggregator';

export interface CourierOption {
  code: string;
  label: string;
  group: CourierGroup;
}

export const COURIER_OPTIONS: CourierOption[] = [
  { code: 'counter', label: 'Counter', group: 'counter' },
  // A store-pickup order (website + collect) has no carrier, but the shop tracks
  // pickup as its own channel — so it is a filter chip like the counter is,
  // mirroring the API's `order_query.WEBSITE_PICKUP_CODE`.
  { code: 'website_pickup', label: 'Store Pickup', group: 'website' },
  { code: 'lalamove', label: 'Lalamove', group: 'website' },
  { code: 'noon_send', label: 'noon Send', group: 'website' },
  // Slider's two vehicle tiers are each their own dispatch courier, so each gets
  // its own filter chip — mirroring the API's `courier_catalog`. (The bare
  // legacy `slider` was retired.)
  { code: 'slider_bike', label: 'Slider (bike)', group: 'website' },
  { code: 'slider_car', label: 'Slider (car)', group: 'website' },
  { code: 'third_party', label: 'Third party', group: 'website' },
  { code: 'talabat', label: 'Talabat', group: 'aggregator' },
  { code: 'keeta', label: 'Keeta', group: 'aggregator' },
  { code: 'noon_food', label: 'Noon Food', group: 'aggregator' },
  { code: 'deliveroo', label: 'Deliveroo', group: 'aggregator' },
  { code: 'careem', label: 'Careem', group: 'aggregator' },
];

const LOGO_BASE = 'https://storage.googleapis.com/mm-product-images/couriers';

/** The convention logo URL for a courier code, or null for the carrier-less
 * synthetic channels (the counter and store pickup). */
export function courierLogo(code: string): string | null {
  if (code === 'counter' || code === 'website_pickup') return null;
  // Slider's bike and car share Slider's badge — the same fallback the API makes.
  const logoCode = code === 'slider_bike' || code === 'slider_car' ? 'slider' : code;
  return `${LOGO_BASE}/${logoCode}.png`;
}

const LABELS = new Map(COURIER_OPTIONS.map(o => [o.code, o.label]));

export function courierLabel(code: string): string {
  return LABELS.get(code) ?? code.replace(/_/g, ' ');
}
