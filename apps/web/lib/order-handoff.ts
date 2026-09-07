/**
 * How the checkout hands an order's identity to the confirmation page without
 * putting the customer's email in the URL (F-WEB-2).
 *
 * The confirmation page has to prove ownership to `GET /orders/{n}` — it is
 * reached by a guest with no session cookie — and the only credential it had was
 * the email, which the checkout therefore appended to the confirmation URL. From
 * there it landed in browser history and, through the tracker, in analytics.
 *
 * Two paths reach the confirmation page, and each has its own credential now:
 *
 *   - the payment gateway return (Stripe, Ziina) carries a signed `token` the
 *     API minted into the `success_url`, read straight off the query string;
 *   - the in-page finishes (cash, a zero total, Apple Pay) never leave the site,
 *     so the checkout stashes the email here in `sessionStorage` and navigates
 *     to a bare `?order_number=` — the email crosses in storage, not the URL.
 *
 * `sessionStorage`, not `localStorage`: this is a one-hop hand-off within the
 * same tab and has no business outliving it. Every access is wrapped because a
 * private window or a storage-blocked browser throws on the accessor itself.
 */

const KEY = 'mm_order_handoff';

export interface OrderHandoff {
  order_number: string;
  /** The order's email — ownership proof for the confirmation lookup. */
  email?: string;
  /** A signed receipt token, if one is already in hand. */
  token?: string;
}

/** Stash the identity of the order just placed, for the confirmation page. */
export function stashOrderHandoff(handoff: OrderHandoff): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(handoff));
  } catch {
    /* private window / storage blocked — the confirmation page falls back to
       whatever the URL carries, so this is a best-effort convenience. */
  }
}

/**
 * The stashed proof for *orderNumber*, or null.
 *
 * Matched on the order number so a stale hand-off from an earlier order is never
 * replayed against a different one — a mismatch reads as "nothing stashed".
 */
export function readOrderHandoff(orderNumber: string): OrderHandoff | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as OrderHandoff;
    return parsed?.order_number === orderNumber ? parsed : null;
  } catch {
    return null;
  }
}

/** Forget the stashed hand-off. */
export function clearOrderHandoff(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* noop */
  }
}
