import type { Address } from './types';

/**
 * The one place an address's stored coordinates become numbers (F-WEB-13).
 *
 * The API sends `latitude`/`longitude` as decimal strings and the web keeps
 * them that way (see `Address`), so every consumer that needs to *do* something
 * numeric with a pin — draw it, measure from it, hand it to a delivery lookup —
 * goes through here rather than sprinkling `Number(a.latitude)` around and
 * hoping. Returns `null` when there is no pin or the stored value will not parse
 * to a finite coordinate: a pinless guest address, or a legacy `''`/`0,0` row,
 * must read as "no location" rather than as a spot in the Atlantic.
 */
export function toLatLng(
  a: Pick<Address, 'latitude' | 'longitude'>,
): { latitude: number; longitude: number } | null {
  if (a.latitude === null || a.longitude === null) return null;
  const latitude = Number(a.latitude);
  const longitude = Number(a.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  // Nobody's home is on the equator off Ghana; a stored 0,0 is a missing pin.
  if (latitude === 0 && longitude === 0) return null;
  return { latitude, longitude };
}
