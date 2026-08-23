/**
 * When a fresh reading from the browser may move the pin, and when it may not.
 *
 * Split out from `LocationProvider` because it is the whole of the judgement
 * and none of the plumbing. A background refresh that gets this wrong either
 * overwrites an address the customer typed, or redraws the site — and re-runs
 * `GET /delivery/area` — every time a handset's GPS wobbles by twenty metres.
 */

import type { Address } from '@/lib/types';
import type { Location, LocationSource } from './types';

/**
 * How far a new reading has to sit from the old one to be worth acting on.
 *
 * Below this it is the same street, and the only thing "moving" buys is
 * another delivery lookup on every navigation. Delivery zones here are
 * district- and emirate-sized, so 250 m rarely straddles a boundary that
 * would change the answer — and never often enough to pay that cost.
 */
export const MOVE_THRESHOLD_M = 250;

/**
 * How stale a cached fix may be before we want the radio woken for a new one.
 *
 * Handed to `getCurrentPosition` as `maximumAge`, so the browser answers from
 * its own cache when it can. Five minutes is short enough that somebody who
 * has actually travelled gets a new answer, and long enough that clicking
 * through four category pages costs one fix rather than four.
 */
export const MAX_FIX_AGE_MS = 5 * 60_000;

/**
 * The sources a silent background reading is allowed to overwrite.
 *
 * `address` and `manual` are the customer speaking — an address they saved, or
 * a pin they dropped themselves — and a handset's opinion about where it is
 * does not get to argue with either. `default` is the shop standing in for an
 * answer we do not have and `geolocation` is an older reading of exactly this
 * kind, so both are ours to replace.
 */
const REFRESHABLE: ReadonlySet<LocationSource> = new Set<LocationSource>([
  'default',
  'geolocation',
]);

const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance between two pins, in metres. */
export function metresBetween(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.sin(dLon / 2) ** 2 * Math.cos(toRad(a.latitude)) * Math.cos(toRad(b.latitude));
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Whether a reading the browser just gave us should become the location. */
export function shouldReplaceWithBrowserFix(
  current: Location,
  fix: { latitude: number; longitude: number },
): boolean {
  if (!REFRESHABLE.has(current.source)) return false;
  // The shop's own coordinates are a placeholder, not a place. Any real
  // reading beats them, however close it happens to land to Al Majaz.
  if (current.source === 'default') return true;
  return metresBetween(current, fix) >= MOVE_THRESHOLD_M;
}

/**
 * The address the location should come from, or null if none of them can say.
 *
 * An address is only a location if somebody actually dropped a pin on it, and
 * plenty have not: the checkout will take a typed street with no marker, and
 * guest addresses stored before the map was mandatory carry `null` for both
 * coordinates. Reading those through `Number()` turns them into a perfectly
 * valid-looking `0, 0` — a spot in the Atlantic — and the site then tells a
 * customer in Nad Al Sheba that we do not deliver to them.
 *
 * So the default address wins only if it has a pin, and otherwise the first
 * one that does. Falling through to a later address is right: a customer with
 * a pinned second address is better served from it than from the ocean.
 */
export function firstPinnedAddress(addresses: readonly Address[]): Address | null {
  const pinned = addresses.filter(
    (a) =>
      Number.isFinite(Number(a.latitude)) &&
      Number.isFinite(Number(a.longitude)) &&
      // `Number(null)` is 0 and `Number('')` is 0, and both arrive here as a
      // finite number. Nobody's home is on the equator off Ghana.
      !(Number(a.latitude) === 0 && Number(a.longitude) === 0),
  );
  return pinned.find((a) => a.is_default) ?? pinned[0] ?? null;
}
