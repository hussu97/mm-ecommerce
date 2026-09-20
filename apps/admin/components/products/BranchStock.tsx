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
  type BranchModifierOptionAvailability,
  type BranchProductAvailability,
  type ModifierOption,
  type ProductModifier,
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

// ─── Modifier-option stock ────────────────────────────────────────────────────
//
// The option-level twin of everything above. `branch_modifier_options` is
// exception-only in exactly the same way, so a miss is in stock and a stockout
// expires on read — the same two conventions, asserted the same way.

/**
 * The modifier-option overrides, indexed for lookup. One fetch, shared by every
 * product row on the list and by the edit screen's grid.
 */
export function useModifierStock() {
  const [rows, setRows] = useState<BranchModifierOptionAvailability[]>([]);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await productsApi.modifierAvailability());
    } catch {
      // A summary that cannot load simply does not draw; the list's job is
      // products, not this column.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const statusOf = useCallback(
    (optionId: string, branchId: string) =>
      modifierOptionStatus(rows, optionId, branchId),
    [rows],
  );

  return { rows, loaded, statusOf, reload: load };
}

/** What one branch says about one modifier option. Same rules as {@link branchStockStatus}. */
export function modifierOptionStatus(
  rows: BranchModifierOptionAvailability[],
  optionId: string,
  branchId: string,
  now: number = Date.now(),
): BranchStockStatus {
  const row = rows.find(
    r => r.modifier_option_id === optionId && r.branch_id === branchId,
  );
  if (!row) return { inStock: true, until: null };
  const lapsed =
    row.out_of_stock_until !== null &&
    new Date(row.out_of_stock_until).getTime() <= now;
  const out = !row.is_in_stock && !lapsed;
  return { inStock: !out, until: out ? row.out_of_stock_until : null };
}

/** The active option ids across all a product's linked modifiers. */
export function productOptionIds(
  productModifiers: ProductModifier[],
): string[] {
  const ids: string[] = [];
  for (const pm of productModifiers) {
    for (const opt of pm.modifier.options) {
      if (opt.is_active) ids.push(opt.id);
    }
  }
  return ids;
}

/** How many of `optionIds` are in stock at one branch. */
export function optionsInStockAt(
  rows: BranchModifierOptionAvailability[],
  optionIds: string[],
  branchId: string,
  now: number = Date.now(),
): number {
  return optionIds.filter(
    id => modifierOptionStatus(rows, id, branchId, now).inStock,
  ).length;
}

/**
 * The compact form for a product row that has modifiers: one badge per branch
 * reading `REF x/y` — options in stock over options total. Green when any
 * filling can still be picked at that branch, red when none can.
 *
 * A product with modifiers can be sold as long as one required option remains,
 * so "how many fillings are left here" is the honest per-branch signal — more
 * so than the product-level flag, which the counter rarely 86s directly.
 */
export function ModifierBranchStockBadges({
  optionIds,
  branches,
  statusOf,
}: {
  optionIds: string[];
  branches: Branch[];
  statusOf: (optionId: string, branchId: string) => BranchStockStatus;
}) {
  if (branches.length === 0 || optionIds.length === 0) {
    return <span className="text-gray-300">—</span>;
  }
  const total = optionIds.length;
  return (
    <div className="flex flex-wrap items-center justify-center gap-1">
      {branches.map(branch => {
        const inStock = optionIds.filter(
          id => statusOf(id, branch.id).inStock,
        ).length;
        const any = inStock > 0;
        return (
          <span
            key={branch.id}
            title={`${branch.name}: ${inStock} of ${total} options in stock`}
            className={cn(
              'font-body text-[10px] tracking-wide px-1.5 py-0.5 rounded-sm border tabular-nums',
              any
                ? 'border-green-200 bg-green-50 text-green-700'
                : 'border-red-200 bg-red-50 text-red-700',
            )}
          >
            {branch.reference} {inStock}/{total}
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

/**
 * The edit screen's per-option, per-branch stock grid.
 *
 * The old modifiers section listed each option as a plain chip and said nothing
 * about where it could actually be made — yet a filling is 86'd per branch, and
 * that is exactly what takes a box off one emirate's website. This draws the
 * real state: every option against every branch, green where it can be picked
 * and red where it cannot, each cell a control. Marking out offers the same
 * three durations as the register; putting back is one click and clears the
 * clock. One product's whole modifier stock, on one screen, for the manager who
 * is not standing in that kitchen.
 */
export function ModifierOptionStockGrid({
  productModifiers,
}: {
  productModifiers: ProductModifier[];
}) {
  const toast = useToast();
  const { branches, loaded: branchesLoaded } = useBranchStock();
  const { statusOf, loaded: stockLoaded, reload } = useModifierStock();
  const [busy, setBusy] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);

  async function change(
    optionId: string,
    branchId: string,
    inStock: boolean,
    duration: StockDuration = 'indefinite',
  ) {
    setBusy(`${optionId}:${branchId}`);
    setMenu(null);
    try {
      await productsApi.setModifierAvailability(optionId, {
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

  if (!branchesLoaded || !stockLoaded) {
    return (
      <p className="text-xs font-body text-gray-400">Loading option stock…</p>
    );
  }
  if (branches.length === 0) {
    return (
      <p className="text-xs font-body text-gray-400">
        No active branches to set stock for.
      </p>
    );
  }

  const groups = [...productModifiers].sort(
    (a, b) => a.display_order - b.display_order,
  );

  return (
    <div className="space-y-4">
      {groups.map(pm => {
        const options = pm.modifier.options
          .filter(o => o.is_active)
          .sort((a, b) => a.display_order - b.display_order);
        if (options.length === 0) return null;
        return (
          <div key={pm.id} className="border border-gray-200">
            <div className="flex items-center justify-between bg-gray-50 px-3 py-2 border-b border-gray-200">
              <span className="text-xs font-body font-medium text-gray-700">
                {pm.modifier.name}{' '}
                <span className="text-gray-400 font-normal">
                  ({pm.modifier.reference})
                </span>
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-gray-100">
                    <th className="px-3 py-2 text-[10px] font-body uppercase tracking-widest text-gray-400 font-medium">
                      Option
                    </th>
                    {branches.map(b => (
                      <th
                        key={b.id}
                        title={b.name}
                        className="px-2 py-2 text-center text-[10px] font-body uppercase tracking-widest text-gray-400 font-medium"
                      >
                        {b.reference}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {options.map(opt => (
                    <ModifierOptionRow
                      key={opt.id}
                      option={opt}
                      branches={branches}
                      statusOf={statusOf}
                      busy={busy}
                      menu={menu}
                      setMenu={setMenu}
                      onChange={change}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      {groups.length === 0 && (
        <p className="text-xs text-gray-400 font-body">No modifiers linked.</p>
      )}
    </div>
  );
}

function ModifierOptionRow({
  option,
  branches,
  statusOf,
  busy,
  menu,
  setMenu,
  onChange,
}: {
  option: ModifierOption;
  branches: Branch[];
  statusOf: (optionId: string, branchId: string) => BranchStockStatus;
  busy: string | null;
  menu: string | null;
  setMenu: (key: string | null) => void;
  onChange: (
    optionId: string,
    branchId: string,
    inStock: boolean,
    duration?: StockDuration,
  ) => void;
}) {
  return (
    <tr>
      <td className="px-3 py-2">
        <span className="text-xs font-body text-gray-700">{option.name}</span>
        {option.price > 0 && (
          <span className="text-[11px] font-body text-gray-400">
            {' '}
            +{Number(option.price).toFixed(2)}
          </span>
        )}
      </td>
      {branches.map(branch => {
        const key = `${option.id}:${branch.id}`;
        const status = statusOf(option.id, branch.id);
        const isBusy = busy === key;
        const isOpen = menu === key;
        return (
          <td key={branch.id} className="px-2 py-2 text-center align-middle">
            <div className="relative inline-block">
              <button
                type="button"
                disabled={isBusy}
                title={
                  status.inStock
                    ? `${branch.name}: in stock — click to mark out`
                    : `${branch.name}: out ${untilLabel(status.until)} — click to put back`
                }
                onClick={() =>
                  status.inStock
                    ? setMenu(isOpen ? null : key)
                    : onChange(option.id, branch.id, true)
                }
                className={cn(
                  'min-w-[3.5rem] font-body text-[11px] px-2 py-1 rounded-sm border transition-colors disabled:opacity-50',
                  status.inStock
                    ? 'border-green-200 bg-green-50 text-green-700 hover:bg-green-100'
                    : 'border-red-200 bg-red-50 text-red-700 hover:bg-red-100',
                )}
              >
                {status.inStock ? 'In' : 'Out'}
              </button>
              {isOpen && (
                <div className="absolute z-10 mt-1 left-1/2 -translate-x-1/2 min-w-[9rem] bg-white border border-gray-200 shadow-md">
                  {STOCK_DURATIONS.map(d => (
                    <button
                      key={d.value}
                      type="button"
                      onClick={() =>
                        onChange(option.id, branch.id, false, d.value)
                      }
                      className="block w-full text-left px-3 py-1.5 text-[11px] font-body text-gray-600 hover:bg-gray-50"
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </td>
        );
      })}
    </tr>
  );
}
