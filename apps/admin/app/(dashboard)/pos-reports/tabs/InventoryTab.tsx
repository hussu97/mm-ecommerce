'use client';

import { posReportsApi } from '@/lib/pos-api';
import { formatCurrency } from '@/lib/utils';
import { Panel, Stat, useReport, windowKey } from './_shared';
import type { CostOfGoods, InventoryValuation } from '@/lib/pos-types';
import type { Window } from '../report-window';


export function InventoryTab({ window, branchId }: { window: Window; branchId: string }) {
  const valuation = useReport<InventoryValuation>(
    () => posReportsApi.inventoryValuation(branchId || undefined),
    branchId,
  );
  const cogs = useReport<CostOfGoods>(
    () => posReportsApi.costOfGoods(window),
    windowKey(window),
  );

  return (
    <div className="space-y-6">
      <Panel loading={cogs.loading} error={cogs.error}>
        {cogs.data && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Cost of goods" value={formatCurrency(cogs.data.cost_of_goods)} />
            <Stat label="Net sales (excl. VAT)" value={formatCurrency(cogs.data.net_sales_excl_tax)} />
            <Stat label="Gross margin" value={formatCurrency(cogs.data.gross_margin)} />
            <Stat label="Margin %" value={`${cogs.data.gross_margin_percent}%`} />
          </div>
        )}
      </Panel>

      <Panel loading={valuation.loading} error={valuation.error}>
        {valuation.data && (
          <>
            <div className="mb-4 grid gap-3 sm:grid-cols-3">
              <Stat label="Items tracked" value={String(valuation.data.items_tracked)} />
              <Stat label="Stock value" value={formatCurrency(valuation.data.total_value)} />
              <Stat
                label="Below minimum"
                value={String(valuation.data.below_minimum_count)}
                hint="Needs reordering"
              />
            </div>
            {valuation.data.below_minimum.length > 0 && (
              <div className="overflow-x-auto rounded border border-gray-200 bg-white">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                      <th className="px-3 py-2 text-left">SKU</th>
                      <th className="px-3 py-2 text-left">Item</th>
                      <th className="px-3 py-2 text-right">On hand</th>
                      <th className="px-3 py-2 text-right">Minimum</th>
                      <th className="px-3 py-2 text-right">Order to par</th>
                    </tr>
                  </thead>
                  <tbody>
                    {valuation.data.below_minimum.map((row) => (
                      <tr key={row.item_id} className="border-b border-gray-100 last:border-0">
                        <td className="px-3 py-2">
                          <code className="text-xs text-gray-500">{row.sku}</code>
                        </td>
                        <td className="px-3 py-2 font-medium">{row.name}</td>
                        <td className="px-3 py-2 text-right">
                          {row.quantity} <span className="text-xs text-gray-400">{row.unit}</span>
                        </td>
                        <td className="px-3 py-2 text-right text-gray-500">{row.minimum_level}</td>
                        <td className="px-3 py-2 text-right font-medium">{row.shortfall}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}


