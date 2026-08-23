'use client';

import { posReportsApi } from '@/lib/pos-api';
import { formatCurrency } from '@/lib/utils';
import { useReport, windowKey, Panel } from './_shared';
import type { PaymentReportRow } from '@/lib/pos-types';
import type { Window } from '../report-window';


export function PaymentsTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<PaymentReportRow[]>(
    () => posReportsApi.payments(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <div className="overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
              <th className="px-3 py-2 text-left">Method</th>
              <th className="px-3 py-2 text-left">Type</th>
              <th className="px-3 py-2 text-right">Count</th>
              <th className="px-3 py-2 text-right">Collected</th>
              <th className="px-3 py-2 text-right">Refunds</th>
              <th className="px-3 py-2 text-right">Net</th>
              <th className="px-3 py-2 text-right">Tips</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r) => (
              <tr key={r.payment_method_id} className="border-b border-gray-100 last:border-0">
                <td className="px-3 py-2 font-medium">{r.name}</td>
                <td className="px-3 py-2 capitalize text-gray-500">{r.type.replace('_', ' ')}</td>
                <td className="px-3 py-2 text-right">{r.transactions}</td>
                <td className="px-3 py-2 text-right">{formatCurrency(r.amount)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{formatCurrency(r.refunds)}</td>
                <td className="px-3 py-2 text-right font-medium">{formatCurrency(r.net)}</td>
                <td className="px-3 py-2 text-right">{formatCurrency(r.tips)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
