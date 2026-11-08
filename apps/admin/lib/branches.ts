import type { Branch } from './pos-types';

/**
 * The branches a *current* choice may name: active and not deleted.
 *
 * `GET /branches` returns inactive branches too, because history (past orders,
 * transfers, reports) must still resolve and filter on a closed branch. A
 * picker that assigns or acts on something now — a PO, a device, staff, a
 * stock count, a setting — must not offer one. `keep` retains ids already
 * selected, so an existing assignment to a closed branch still shows and can
 * be removed rather than silently vanishing from the form.
 */
export function liveBranches<B extends Pick<Branch, 'id' | 'is_active' | 'deleted_at'>>(
  branches: B[],
  keep: Iterable<string | null | undefined> = [],
): B[] {
  const kept = new Set([...keep].filter(Boolean));
  return branches.filter((b) => (b.is_active && !b.deleted_at) || kept.has(b.id));
}
