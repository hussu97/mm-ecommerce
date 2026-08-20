'use client';

/**
 * Which branches can actually make a product, and putting it back when one cannot.
 *
 * The register has been able to mark an item out at its own branch since the
 * "86 it" button existed, and the console could not even see the result. That
 * was survivable while the website hid a product only when *every* branch was
 * out of it. It stopped being survivable when the storefront started answering
 * per branch: a cake now disappears from one emirate's website and the only
 * screen that explained why was an iPad in that kitchen.
 *
 * Two surfaces, one component, because the badge on the list and the badge on
 * the edit screen are the same claim and must not be able to word it
 * differently.
 */

import { useCallback, useEffect, useState } from 'react';

import { productsApi } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import {
  STOCK_DURATIONS,
  type BranchProductAvailability,
  type StockDuration,
} from '@/lib/types';
import { Badge, Button } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';
import { cn, formatDateTime } from '@/lib/utils';

/**
 * The override rows indexed for lookup, and the branches they belong to.
 *
 * One fetch of each, shared by every row on a list of a thousand products. The
 * tables are exception-only so the whole answer is a few dozen rows, which is
 * what makes a per-branch column affordable at all.
 */
export function useBranchStock() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [rows, setRows] = useState<BranchProductAvailability[]>([]);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const [allBranches, availability] = await Promise.all([
        branchesApi.list(),
        productsApi.branchAvailability(),
      ]);
      setBranches(allBranches.filter(b => b.is_active));
      setRows(availability);
    } catch {
      // A column that cannot load is a column that does not draw. It must not
      // take the product list down with it — the list's own job is products.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const statusOf = useCallback(
    (productId: string, branchId: string) =>
      branchStockStatus(rows, productId, branchId),
    [rows],
  );

  return { branches, rows, loaded, statusOf, reload: load };
}

export interface BranchStockStatus {
  inStock: boolean;
  /** When it comes back, or null for "until somebody puts it back". */
  until: string | null;
}

/**
 * What one branch says about one product.
 *
 * Two conventions, both easy to get backwards and both asserted in
 * `BranchStock.test.ts`.
 *
 * **A miss is in stock.** `branch_products` holds a row only where a branch
 * differs from the catalogue, so "no row" means "sells it as normal". Reading
 * absence as unavailable would paint every branch red for every product nobody
 * has ever touched — which is most of them.
 *
 * **A stockout expires on read.** The API compares `out_of_stock_until` with
 * now on every question it is asked, so a branch that marked something out
 * until close is selling it again the moment the clock passes, whether or not
 * any sweep has run. A console left open on a desk has to do the same, or it
 * goes on showing an hour that ended at lunchtime.
 *
 * `now` is a parameter so the tests can pin a clock rather than depend on the
 * minute they run at.
 */
export function branchStockStatus(
  rows: BranchProductAvailability[],
  productId: string,
  branchId: string,
  now: number = Date.now(),
): BranchStockStatus {
  const row = rows.find(
    r => r.product_id === productId && r.branch_id === branchId,
  );
  if (!row) return { inStock: true, until: null };
  const lapsed =
    row.out_of_stock_until !== null &&
    new Date(row.out_of_stock_until).getTime() <= now;
  const out = !row.is_active || (!row.is_in_stock && !lapsed);
  return { inStock: !out, until: out ? row.out_of_stock_until : null };
}

/**
 * The compact form, for a table cell.
 *
 * Branch references rather than names — `K001`, `B001` — because the column has
 * to survive a dozen branches in the width of a table cell, and the reference
 * is what the people who read this screen already say to each other.
 */
export function BranchStockBadges({
  productId,
  branches,
  statusOf,
}: {
  productId: string;
  branches: Branch[];
  statusOf: (productId: string, branchId: string) => BranchStockStatus;
}) {
  if (branches.length === 0) return <span className="text-gray-300">—</span>;
  return (
    <div className="flex flex-wrap items-center justify-center gap-1">
      {branches.map(branch => {
        const status = statusOf(productId, branch.id);
        return (
          <span
            key={branch.id}
            title={
              status.inStock
                ? `${branch.name}: on sale`
                : `${branch.name}: off sale ${untilLabel(status.until)}`
            }
            className={cn(
              'font-body text-[10px] tracking-wide px-1.5 py-0.5 rounded-sm border',
              status.inStock
                ? 'border-green-200 bg-green-50 text-green-700'
                : 'border-red-200 bg-red-50 text-red-700',
            )}
          >
            {branch.reference}
          </span>
        );
      })}
    </div>
  );
}

/**
 * The full form, for the edit screen: one row per branch, with the controls.
 *
 * Off sale is a menu of the three durations rather than a single button, for
 * the same reason the register's is: a tray twenty minutes from the oven and a
 * supplier who has not delivered are different facts, and the first one left as
 * indefinite is an item still off sale the next morning.
 */
export function BranchStockPanel({ productId }: { productId: string }) {
  const toast = useToast();
  const { branches, loaded, statusOf, reload } = useBranchStock();
  const [busy, setBusy] = useState<string | null>(null);

  async function change(
    branchId: string,
    inStock: boolean,
    duration: StockDuration = 'indefinite',
  ) {
    setBusy(branchId);
    try {
      await productsApi.setBranchAvailability(productId, {
        branch_id: branchId,
        is_in_stock: inStock,
        duration,
      });
      await reload();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (!loaded) {
    return (
      <p className="text-xs font-body text-gray-400">Loading branch stock…</p>
    );
  }

  if (branches.length === 0) {
    return (
      <p className="text-xs font-body text-gray-400">
        No active branches to set stock for.
      </p>
    );
  }

  return (
    <div className="divide-y divide-gray-100 border border-gray-200">
      {branches.map(branch => {
        const status = statusOf(productId, branch.id);
        const isBusy = busy === branch.id;
        return (
          <div
            key={branch.id}
            className="flex flex-wrap items-center gap-3 px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <div className="text-sm font-body text-gray-800">{branch.name}</div>
              <div className="text-[11px] font-body text-gray-400">
                {branch.reference}
              </div>
            </div>

            <Badge variant={status.inStock ? 'success' : 'danger'}>
              {status.inStock ? 'On sale' : 'Off sale'}
            </Badge>

            {/* Only where there is a clock to report. "Off sale" with no
                sentence beside it is the indefinite case, and it reads as a
                different instruction — somebody has to come back and do
                something. */}
            {!status.inStock && (
              <span className="text-[11px] font-body text-gray-500">
                back {untilLabel(status.until)}
              </span>
            )}

            <div className="flex items-center gap-2">
              {status.inStock ? (
                STOCK_DURATIONS.map(option => (
                  <Button
                    key={option.value}
                    size="sm"
                    variant="ghost"
                    disabled={isBusy}
                    onClick={() => change(branch.id, false, option.value)}
                  >
                    {option.label}
                  </Button>
                ))
              ) : (
                <Button
                  size="sm"
                  disabled={isBusy}
                  onClick={() => change(branch.id, true)}
                >
                  Put back on sale
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * "at 21 Aug 2026, 16:30", or "when you put it back".
 *
 * The indefinite case is said the other way round from the register's "until
 * you put it back" because it follows "back" here rather than "off". Same fact,
 * and the same distinction that matters: a clock, or a person.
 */
function untilLabel(until: string | null): string {
  return until ? `at ${formatDateTime(until)}` : 'when you put it back';
}
