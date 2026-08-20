/**
 * The kitchen the storefront is browsing as, written where the server can see it.
 *
 * The catalogue has to match the branch that would bake the order — a shopper
 * offered a cake their own kitchen has marked out is a shopper the checkout
 * will refuse. The branch comes from `GET /delivery/area`, which resolves the
 * pin exactly as order placement does.
 *
 * **A cookie rather than `localStorage`, and that is the whole reason this file
 * exists.** The location lives in `localStorage`, which the server cannot read,
 * and the category and product pages are server-rendered. Filtering in the
 * browser instead would mean shipping HTML full of cakes and then deleting them
 * on first paint, in front of the customer. A cookie is the one piece of
 * browser state a React Server Component can ask for.
 *
 * Deliberately not `httpOnly` — this is written by client code and read by the
 * server, which is the opposite of the usual direction — and deliberately not a
 * secret: a branch id names a public menu.
 */

export const BRANCH_COOKIE = 'mm_branch';

/** A year. The pin it was derived from outlives any session. */
const MAX_AGE = 60 * 60 * 24 * 365;

/**
 * Record the branch, or clear it when there is none.
 *
 * Clearing matters as much as setting. A pin we do not serve resolves to no
 * kitchen, and leaving the previous one in place would filter the catalogue by
 * a branch that has nothing to do with where the customer now says they are.
 * No cookie means "we do not know", and the server's answer to that is the
 * widest one: everything some branch can still make.
 */
export function rememberBranch(branchId: string | null | undefined): void {
  if (typeof document === 'undefined') return;
  const base = `${BRANCH_COOKIE}=`;
  const attrs = `Path=/; Max-Age=${branchId ? MAX_AGE : 0}; SameSite=Lax`;
  document.cookie = `${base}${branchId ?? ''}; ${attrs}`;
}

/** What is currently recorded, for code that has to reason about it client-side. */
export function readBranch(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${BRANCH_COOKIE}=([^;]*)`),
  );
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}
