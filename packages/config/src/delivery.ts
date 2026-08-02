/**
 * Delivery constants shared by the frontends.
 *
 * This file used to hold a nine-row fee table keyed by emirate, mirrored by
 * hand from the Python service. Both halves of that are gone: the fee comes
 * from a versioned polygon map priced against the customer's actual pin, and
 * `POST /delivery/quote` is the only thing that knows it. A copy here could
 * only ever be a second, staler answer to a question the API already answers —
 * and it was already wrong by the time it was noticed.
 *
 * What survives is the small amount that genuinely does not vary.
 */

/**
 * The basket value that earns free delivery, in AED.
 *
 * Deliberately the same in every zone. It costs several times more to reach
 * Fujairah than Sharjah, but a threshold that moved with the address would be
 * the one place the delivery map became visible to the customer, and "free
 * over 200" has to read the same everywhere.
 *
 * The authority is `delivery_settings.free_delivery_threshold`, returned on
 * every quote as `free_threshold`. This is the fallback for rendering a
 * marketing line before any quote has been taken — never for pricing.
 */
export const FREE_DELIVERY_THRESHOLD = 200;

/**
 * Charged when no polygon matches the pin. The API returns the live value as
 * `base_fee`; this exists for the same render-before-quote case.
 */
export const FALLBACK_DELIVERY_FEE = 50;
