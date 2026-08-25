/**
 * Where the customer is, and how confident we are about it.
 *
 * `source` is the whole point of this type. The same two numbers mean very
 * different things depending on where they came from, and the copy has to
 * change with them: an address the customer saved is a fact and can be stated
 * ("Delivering to Al Majaz"), a browser geolocation reading is a good guess and
 * should be offered rather than asserted, and the shop's own coordinates are a
 * placeholder that must never be described as the customer's location at all.
 *
 * Getting that wrong is how a site tells somebody in Abu Dhabi that their
 * delivery is free.
 */
export type LocationSource =
  /** A saved address — the default one, or the last one they ordered to. */
  | 'address'
  /** The browser told us, after the customer allowed it. */
  | 'geolocation'
  /** They picked it on the map themselves. */
  | 'manual'
  /** Nobody told us anything. These are the Sharjah kitchen's coordinates. */
  | 'default';

export interface Location {
  latitude: number;
  longitude: number;
  source: LocationSource;
  /** A human label, when we have one worth showing. */
  label?: string | null;
}

/** How fast we deliver to a place. Mirrors `speed` on `GET /delivery/area`. */
export type DeliverySpeed = 'express' | 'same_day' | 'next_day';

/**
 * What delivery looks like where the customer is.
 *
 * Straight from `GET /delivery/area`, which deliberately names a place and a
 * speed and never a carrier.
 */
export interface DeliveryArea {
  serviceable: boolean;
  zone_name: string | null;
  /** Null where the fee is a live courier quote and needs a basket to exist. */
  delivery_fee: number | null;
  /** The basket that earns free delivery *here*. Zero is real — Sharjah. */
  free_threshold: number | null;
  free_delivery_available: boolean;
  speed: DeliverySpeed;
  /**
   * For an `express` pin, the courier's promised minutes ready-to-door — the
   * number the badge says out loud, sourced server-side from the same courier
   * row the checkout quotes, so the card and the checkout agree. Null for
   * `same_day`/`next_day`, and null for an express zone whose courier promises a
   * day rather than minutes, where the badge falls back to its own wording.
   */
  express_minutes?: number | null;
  /**
   * The kitchen this pin resolves to, or null where we do not serve it.
   *
   * Handed straight back on catalogue reads so the shelf matches the branch
   * that would bake the order. Null is an answer and not a gap — the server
   * reads it as "show what any branch can make", which is the wider and safer
   * of the two.
   */
  branch_id?: string | null;
}

/** The shop itself, and the fallback when we know nothing. */
export const DEFAULT_LOCATION: Location = {
  latitude: 25.3304139,
  longitude: 55.3736131,
  source: 'default',
  label: null,
};

export const LOCATION_STORAGE_KEY = 'mm_location';

/**
 * Whether we have already asked this browser for its location.
 *
 * Separate from whether we *have* a location, because "asked and declined" and
 * "never asked" must not look the same. Asking again on every visit is how a
 * permission prompt turns into a reason to leave.
 */
export const LOCATION_ASKED_KEY = 'mm_location_asked';
