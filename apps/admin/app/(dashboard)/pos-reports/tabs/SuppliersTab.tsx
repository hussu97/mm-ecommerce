'use client';

import { posReportsApi } from '@/lib/pos-api';
import { formatCurrency } from '@/lib/utils';
import { Panel, ReportTable, useReport, windowKey } from './_shared';
import type { SupplierAnalysisRow } from '@/lib/pos-types';
import type { Window } from '../report-window';


export function SuppliersTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<SupplierAnalysisRow[]>(
    () => posReportsApi.suppliersAnalysis(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <ReportTable
        head={['Supplier', 'Purchase orders', 'Total spend']}
        rows={(data ?? []).map((r) => [r.supplier, r.purchase_orders, formatCurrency(r.total_spend)])}
      />
    </Panel>
  );
}
