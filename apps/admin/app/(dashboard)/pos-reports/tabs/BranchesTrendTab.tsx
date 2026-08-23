'use client';

import { posReportsApi } from '@/lib/pos-api';
import { formatCurrency } from '@/lib/utils';
import { Panel, ReportTable, useReport, windowKey } from './_shared';
import type { BranchTrendRow } from '@/lib/pos-types';
import type { Window } from '../report-window';


export function BranchesTrendTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<BranchTrendRow[]>(
    () => posReportsApi.branchesTrend(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <ReportTable
        head={['Branch', 'Day', 'Orders', 'Net sales', 'Avg order']}
        rows={(data ?? []).map((r) => [
          r.branch,
          r.business_date,
          r.orders,
          formatCurrency(r.net_sales),
          formatCurrency(r.average_order_value),
        ])}
      />
    </Panel>
  );
}
