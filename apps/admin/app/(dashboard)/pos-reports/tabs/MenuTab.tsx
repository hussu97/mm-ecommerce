'use client';

import { posReportsApi } from '@/lib/pos-api';
import { Badge } from '@/components/ui';
import { formatCurrency } from '@/lib/utils';
import { useReport, windowKey, Panel } from './_shared';
import type { MenuEngineeringRow } from '@/lib/pos-types';
import type { Window } from '../report-window';


const CLASSIFICATION_STYLE: Record<
  MenuEngineeringRow['classification'],
  { label: string; variant: 'success' | 'info' | 'warning' | 'danger' }
> = {
  star: { label: 'Star', variant: 'success' },
  plough_horse: { label: 'Plough horse', variant: 'info' },
  puzzle: { label: 'Puzzle', variant: 'warning' },
  dog: { label: 'Dog', variant: 'danger' },
};

export function MenuTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<MenuEngineeringRow[]>(
    () => posReportsApi.menuEngineering(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <p className="mb-3 text-xs text-gray-500 font-body">
        Items are classified against the period average: popular and profitable is a star,
        popular but thin is a plough horse, profitable but slow is a puzzle.
      </p>
      <div className="overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
              <th className="px-3 py-2 text-left">Product</th>
              <th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Net sales</th>
              <th className="px-3 py-2 text-right">Cost</th>
              <th className="px-3 py-2 text-right">Margin</th>
              <th className="px-3 py-2 text-right">Margin %</th>
              <th className="px-3 py-2 text-left">Class</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r) => {
              const style = CLASSIFICATION_STYLE[r.classification];
              return (
                <tr key={r.key} className="border-b border-gray-100 last:border-0">
                  <td className="px-3 py-2 font-medium">{r.label}</td>
                  <td className="px-3 py-2 text-right">{r.quantity ?? 0}</td>
                  <td className="px-3 py-2 text-right">{formatCurrency(r.net_sales)}</td>
                  <td className="px-3 py-2 text-right text-gray-500">{formatCurrency(r.cost)}</td>
                  <td className="px-3 py-2 text-right">{formatCurrency(r.margin)}</td>
                  <td className="px-3 py-2 text-right">{(r.margin_percent * 100).toFixed(1)}%</td>
                  <td className="px-3 py-2">
                    <Badge variant={style.variant}>{style.label}</Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
