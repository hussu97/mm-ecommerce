/**
 * The delivery zone the storefront is browsing as, written where the server can
 * see it.
 *
 * The catalogue has to match what the customer can actually be delivered — a
 * shopper offered a cake no kitchen that serves them can bake is a shopper the
 * checkout will refuse. The zone is the polygon the pin falls in, from
 * `GET /delivery/area`, which resolves the pin exactly as order placement does.
 * A polygon is served by a ranked list of kitchens, and the catalogue shows the
 * union of everything any of them can make; picking the one that bakes the
 * order is left to the server at preview and placement time.
 *
 * **A polygon id rather than a single branch, and a cookie rather than
 * `localStorage`.** The polygon is the stable browsing key: there are ~97 of
 * them, good for SSR cache fragmentation, and it does not change under the
 * shopper when a kitchen goes out of an item. The location lives in
 * `localStorage`, which the server cannot read, and the category and product
 * pages are server-rendered. Filtering in the browser instead would mean
 * shipping HTML full of cakes and then deleting them on first paint, in front
 * of the customer. A cookie is the one piece of browser state a React Server
 * Component can ask for.
 *
 * Deliberately not `httpOnly` — this is written by client code and read by the
 * server, which is the opposite of the usual direction — and deliberately not a
 * secret: a polygon id names a public delivery area.
 */

export const ZONE_COOKIE = 'mm_zone';

/**
 * The cookie this replaced (a single branch id). Nothing reads it any more, but
 * it was written with a year-long `Max-Age`, so every returning shopper carries
 * a dead value until it expires. Cleared alongside the first `mm_zone` write.
 */
const LEGACY_BRANCH_COOKIE = 'mm_branch';

/** A year. The pin it was derived from outlives any session. */
const MAX_AGE = 60 * 60 * 24 * 365;

/**
 * Record the zone, or clear it when there is none.
 *
 * Clearing matters as much as setting. A pin we do not serve resolves to no
 * polygon, and leaving the previous one in place would filter the catalogue by
 * a zone that has nothing to do with where the customer now says they are.
 * No cookie means "we do not know", and the server's answer to that is the
 * widest one: everything the whole website-delivery union can still make.
 */
export function rememberZone(polygonId: string | null | undefined): void {
  if (typeof document === 'undefined') return;
  const base = `${ZONE_COOKIE}=`;
  const attrs = `Path=/; Max-Age=${polygonId ? MAX_AGE : 0}; SameSite=Lax`;
  document.cookie = `${base}${polygonId ?? ''}; ${attrs}`;
  // Retire the pre-multi-branch cookie so it stops shadowing the zone for the
  // rest of its year. A no-op once it is gone.
  document.cookie = `${LEGACY_BRANCH_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/** What is currently recorded, for code that has to reason about it client-side. */
export function readZone(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${ZONE_COOKIE}=([^;]*)`),
  );
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}
