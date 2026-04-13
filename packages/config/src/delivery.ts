/**
 * Delivery fee constants — single source of truth for the frontend.
 *
 * These values MUST stay in sync with the Python backend:
 *   apps/api/app/services/delivery_service.py
 *
 * Standard zones (Dubai, Sharjah, Ajman):  35 AED
 * Remote zones (all others):               50 AED
 * Free threshold:                         200 AED (post-discount subtotal)
 */

export type RegionCode =
  | 'dubai'
  | 'sharjah'
  | 'ajman'
  | 'abu_dhabi'
  | 'fujairah'
  | 'ras_al_khaimah'
  | 'umm_al_quwain'
  | 'al_ain'
  | 'rest_of_uae';

export const DELIVERY_FEES: Record<RegionCode, number> = {
  dubai: 35,
  sharjah: 35,
  ajman: 35,
  abu_dhabi: 50,
  fujairah: 50,
  ras_al_khaimah: 50,
  umm_al_quwain: 50,
  al_ain: 50,
  rest_of_uae: 50,
};

export const FREE_DELIVERY_THRESHOLD = 200;

/**
 * Calculate the delivery fee for a given method, region, and subtotal.
 * Mirrors `delivery_service.calculate_fee()` in the Python API.
 */
export function calcDeliveryFee(
  method: string,
  region: string,
  subtotal: number,
): number {
  if (method === 'pickup') return 0;
  if (subtotal >= FREE_DELIVERY_THRESHOLD) return 0;
  return DELIVERY_FEES[region as RegionCode] ?? 50;
}
