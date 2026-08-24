'use client';

import { posReportsApi } from '@/lib/pos-api';
import { formatCurrency } from '@/lib/utils';
import { useReport, windowKey, Panel } from './_shared';
import type { TaxReportRow } from '@/lib/pos-types';
import type { Window } from '../report-window';


export function TaxesTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<TaxReportRow[]>(
    () => posReportsApi.taxes(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <div className="overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
              <th className="px-3 py-2 text-left">Tax</th>
              <th className="px-3 py-2 text-right">Rate</th>
              <th className="px-3 py-2 text-right">Taxable amount</th>
              <th className="px-3 py-2 text-right">Tax collected</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r) => (
              <tr key={`${r.name}-${r.rate}`} className="border-b border-gray-100 last:border-0">
                <td className="px-3 py-2 font-medium">{r.name}</td>
                <td className="px-3 py-2 text-right">{r.rate_percent}%</td>
                <td className="px-3 py-2 text-right">{formatCurrency(r.taxable_amount)}</td>
                <td className="px-3 py-2 text-right font-medium">{formatCurrency(r.tax_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
