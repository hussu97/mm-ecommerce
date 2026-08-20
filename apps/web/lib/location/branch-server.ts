import { cookies } from 'next/headers';

import { BRANCH_COOKIE } from './branch-cookie';

/**
 * The kitchen this request is browsing as, for a server-rendered page.
 *
 * Reading the cookie is what makes a route dynamic, so this is called only by
 * pages whose contents genuinely depend on the branch — the category grid, a
 * product page, the cart's add-on tray. A page that does not filter products
 * must not call it, or it pays for a render it did not need.
 *
 * Returns `null` for a request that has never resolved a location: a crawler,
 * or a first visit before `LocationProvider` has had its answer. That is the
 * honest state and the API reads it as "show what any branch can make" — which
 * is also what keeps the indexable catalogue the whole catalogue rather than
 * one kitchen's shelf.
 */
export async function browsingBranch(): Promise<string | null> {
  const store = await cookies();
  const value = store.get(BRANCH_COOKIE)?.value?.trim();
  return value ? value : null;
}

/**
 * `&branch_id=…`, or nothing at all.
 *
 * A fragment rather than a param object because every caller is building a
 * query string by hand already, and an empty string is exactly what "no branch"
 * should add to one.
 */
export function branchParam(branchId: string | null): string {
  return branchId ? `&branch_id=${encodeURIComponent(branchId)}` : '';
}
