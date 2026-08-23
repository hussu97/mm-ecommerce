'use client';

import { posReportsApi } from '@/lib/pos-api';
import { formatCurrency } from '@/lib/utils';
import { Panel, ReportTable, useReport, windowKey } from './_shared';
import type { TableUtilizationRow } from '@/lib/pos-types';
import type { Window } from '../report-window';


export function TableUtilizationTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<TableUtilizationRow[]>(
    () => posReportsApi.tableUtilization(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <ReportTable
        head={['Table', 'Section', 'Seats', 'Turns', 'Covers', 'Avg mins', 'Net sales', 'Per seat']}
        rows={(data ?? []).map((r) => [
          r.table,
          r.section,
          r.seats,
          r.turns,
          r.covers,
          r.average_minutes,
          formatCurrency(r.net_sales),
          formatCurrency(r.sales_per_seat),
        ])}
      />
    </Panel>
  );
}
