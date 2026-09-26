'use client';

/**
 * Recipe cost against price, as `/products/costs` computed it.
 *
 * A product without options shows its own recipe cost against its price; one
 * with options shows every option, priced and costed the way it sells (with the
 * product's base). All figures, percentages included, are the API's — nothing
 * here does arithmetic (canon rule 10). The list asks once per page, with that
 * page's ids, so it is one request however many rows.
 */

import { useEffect, useState } from 'react';
import { productsApi, type ProductCost } from '@/lib/api';
import { cn, formatCurrency } from '@/lib/utils';

const NONE: Map<string, ProductCost> = new Map();

/** Costs for a set of products, keyed by product id. Empty (not an error) for
 *  a user who may not see recipe costs: the columns just stay blank. */
export function useProductCosts(productIds: string[]): Map<string, ProductCost> {
  // Tagged with the ids they answer, so a stale page's costs never show.
  const [loaded, setLoaded] = useState<{ key: string; costs: Map<string, ProductCost> }>();
  const key = productIds.join(',');
  useEffect(() => {
    if (!key) return;
    let live = true;
    productsApi
      .costs(key.split(','))
      .then(rows => live && setLoaded({ key, costs: new Map(rows.map(r => [r.product_id, r])) }))
      .catch(() => live && setLoaded({ key, costs: NONE }));
    return () => {
      live = false;
    };
  }, [key]);
  return loaded?.key === key ? loaded.costs : NONE;
}

function CostFigure({ cost, missing }: { cost: number | null; missing?: string }) {
  if (cost === null) {
    return <span className="text-amber-700" title={missing}>no recipe</span>;
  }
  return <span>{formatCurrency(cost)}</span>;
}

/** One line per option, fixed height so the Price, Cost and % columns line up
 *  row for row. */
const LINE = 'flex h-5 items-center justify-end gap-2 whitespace-nowrap';

/**
 * The product list's Price cell. With options: one line per option, its name
 * and the price it sells at (base + option). Without: the product's price.
 * `fallback` is what shows before costs arrive, or for a user who cannot see
 * recipe costs.
 */
export function PriceCell({
  cost,
  fallback,
}: {
  cost: ProductCost | undefined;
  fallback: React.ReactNode;
}) {
  if (!cost) return <>{fallback}</>;
  if (cost.options.length === 0) {
    return <div className={cn(LINE, 'tabular-nums')}>{formatCurrency(cost.price)}</div>;
  }
  return (
    <div className="text-[11px] font-body">
      {cost.options.map(o => (
        <div key={o.modifier_option_id} className={LINE}>
          <span className="max-w-[9rem] truncate text-gray-500" title={o.name}>{o.name}</span>
          <span className="tabular-nums text-gray-800">{formatCurrency(o.price)}</span>
        </div>
      ))}
    </div>
  );
}

/** The product list's Cost cell, aligned line for line with `PriceCell`. */
export function CostCell({ cost }: { cost: ProductCost | undefined }) {
  if (!cost) return <span className="text-gray-300">—</span>;
  if (cost.options.length === 0) {
    return (
      <div className={cn(LINE, 'tabular-nums')}>
        {cost.consumes_stock ? (
          <CostFigure cost={cost.cost} missing="This product has no active recipe." />
        ) : (
          <span className="text-[11px] text-gray-400">not tracked</span>
        )}
      </div>
    );
  }
  return (
    <div className="text-[11px] font-body">
      {cost.options.map(o => (
        <div key={o.modifier_option_id} className={cn(LINE, 'tabular-nums text-gray-600')}>
          <CostFigure cost={o.cost} missing="This option has no active recipe." />
        </div>
      ))}
    </div>
  );
}

/** The product list's Cost % cell: cost as a % of price, per line. */
export function CostPctCell({ cost }: { cost: ProductCost | undefined }) {
  if (!cost) return <span className="text-gray-300">—</span>;
  const lines =
    cost.options.length === 0
      ? [{ key: cost.product_id, pct: cost.cost_pct }]
      : cost.options.map(o => ({ key: o.modifier_option_id, pct: o.cost_pct }));
  return (
    <div className={cn(cost.options.length > 0 && 'text-[11px]', 'font-body')}>
      {lines.map(l => (
        <div key={l.key} className={cn(LINE, 'tabular-nums text-gray-500')}>
          {l.pct === null ? '—' : `${l.pct.toFixed(1)}%`}
        </div>
      ))}
    </div>
  );
}

/** The product page's cost panel: the same figures as the list, as a table. */
export function ProductCostPanel({ productId }: { productId: string }) {
  const costs = useProductCosts([productId]);
  const cost = costs.get(productId);
  if (!cost) return null;
  const rows =
    cost.options.length > 0
      ? cost.options.map(o => ({
          key: o.modifier_option_id,
          label: `${o.modifier_name}: ${o.name}`,
          price: o.price,
          cost: o.cost,
          pct: o.cost_pct,
        }))
      : [{ key: cost.product_id, label: 'This product', price: cost.price, cost: cost.cost, pct: cost.cost_pct }];
  return (
    <section className="mt-8 border border-gray-200 bg-white px-4 py-3">
      <h2 className="font-display text-lg text-gray-800">Cost &amp; margin</h2>
      <p className="mb-3 text-xs font-body text-gray-400">
        What one sells for against what its recipe costs today (current stock cost across all
        kitchens).{' '}
        {cost.options.length > 0 &&
          'Each option is priced and costed as it sells — with this product’s base price and recipe.'}
      </p>
      {cost.options.length > 0 && cost.missing_recipe && (
        <p className="mb-2 text-xs font-body text-amber-700">
          This product has no recipe of its own, so only its options’ recipes are costed.
        </p>
      )}
      {cost.options.length === 0 && !cost.consumes_stock ? (
        <p className="text-xs font-body text-gray-400">This product does not track stock.</p>
      ) : (
        <table className="w-full text-xs font-body">
          <thead>
            <tr className="border-b border-gray-200 text-[11px] uppercase tracking-wider text-gray-400">
              <th className="py-1.5 text-left font-normal">Item</th>
              <th className="py-1.5 text-right font-normal">Price</th>
              <th className="py-1.5 text-right font-normal">Cost</th>
              <th className="py-1.5 text-right font-normal">% of price</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.key} className="border-b border-gray-100 last:border-0 text-gray-600">
                <td className="py-1.5">{r.label}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCurrency(r.price)}</td>
                <td className="py-1.5 text-right tabular-nums">
                  <CostFigure cost={r.cost} missing="No active recipe." />
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {r.pct === null ? '—' : `${r.pct.toFixed(1)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
