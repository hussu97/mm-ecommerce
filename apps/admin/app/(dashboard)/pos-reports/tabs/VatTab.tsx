'use client';

// POS Reports → VAT. Output VAT collected (sales) versus input VAT recoverable
// (costs), per legal entity, over the shared reporting window. Reads the derived
// `vat_ledger_entries` cache through `/pos/reports/vat-ledger`; the money is
// computed server-side, this only lays it out (rule 10).

import { useEffect, useState } from 'react';

import { legalEntitiesApi, posReportsApi } from '@/lib/pos-api';
import type { LegalEntity, VatLedgerResponse, VatLedgerRow } from '@/lib/pos-types';
import { Badge, Select } from '@/components/ui';
import { formatCurrency } from '@/lib/utils';

import type { Window } from '../report-window';
import { Panel, useReport, windowKey } from './_shared';

// The categories the ledger splits each direction into, in the order they read.
const CATEGORY_LABELS: Record<string, string> = {
  sales_output: 'Sales',
  sales_refund: 'Refunds',
  aggregator_commission: 'Aggregator commission',
  payment_processing: 'Payment processing',
  courier_fees: 'Courier fees',
  raw_goods: 'Raw goods',
  marketplace_marketing: 'Marketplace marketing',
  marketplace_cancellation: 'Marketplace cancellation',
  marketplace_period_charges: 'Marketplace platform & period charges',
};

const categoryLabel = (category: string): string =>
  CATEGORY_LABELS[category] ?? category.replaceAll('_', ' ');

export function VatTab({ window }: { window: Window }) {
  const [entityId, setEntityId] = useState('');
  const [entities, setEntities] = useState<LegalEntity[]>([]);

  useEffect(() => {
    void legalEntitiesApi.list().then(setEntities).catch(() => setEntities([]));
  }, []);

  const report = useReport<VatLedgerResponse>(
    () =>
      posReportsApi.vatLedger({
        date_from: window.date_from,
        date_to: window.date_to,
        legal_entity_id: entityId || undefined,
      }),
    windowKey(window, entityId),
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <Select
          label="Legal entity"
          value={entityId}
          onChange={(e) => setEntityId(e.target.value)}
          options={entities.map((entity) => ({ value: entity.id, label: entity.brand_name || entity.legal_name }))}
          placeholder="All legal entities"
          className="w-64"
        />
      </div>

      <Panel
        loading={report.loading}
        error={report.error}
        empty={!report.data?.summary.length}
      >
        {report.data && (
          <div className="space-y-8">
            {report.data.summary.map((entity) => {
              const rows = report.data!.rows.filter((r) => r.legal_entity_id === entity.legal_entity_id);
              const output = rows.filter((r) => r.direction === 'output');
              const input = rows.filter((r) => r.direction === 'input');
              const owed = Number(entity.net_vat_position) >= 0;
              return (
                <section key={entity.legal_entity_id} className="space-y-4">
                  <header className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 pb-3">
                    <div className="flex items-center gap-2">
                      <h2 className="font-display text-lg text-primary tracking-wide">{entity.legal_entity_name}</h2>
                      <Badge variant={entity.vat_registered ? 'success' : 'neutral'}>
                        {entity.vat_registered ? 'VAT registered' : 'Not VAT registered'}
                      </Badge>
                    </div>
                    <div className="text-right">
                      <p className="text-[11px] uppercase tracking-widest text-gray-400 font-body">
                        Net VAT position
                      </p>
                      <p className={`font-display text-xl tabular-nums ${owed ? 'text-primary' : 'text-green-700'}`}>
                        {formatCurrency(entity.net_vat_position)}
                      </p>
                      <p className="text-[11px] text-gray-400 font-body">
                        {owed ? 'Owed to FTA' : 'Reclaimable from FTA'} · output {formatCurrency(entity.output_vat)} − input {formatCurrency(entity.input_vat_recoverable)}
                      </p>
                    </div>
                  </header>

                  <VatBlock title="Output — VAT collected" rows={output} />
                  <VatBlock title="Input — VAT recoverable" rows={input} showRecoverable />
                </section>
              );
            })}
          </div>
        )}
      </Panel>
    </div>
  );
}

function VatBlock({
  title,
  rows,
  showRecoverable = false,
}: {
  title: string;
  rows: VatLedgerRow[];
  showRecoverable?: boolean;
}) {
  const netTotal = rows.reduce((sum, r) => sum + Number(r.net_value), 0);
  const vatTotal = rows.reduce((sum, r) => sum + Number(r.vat_amount), 0);
  const grossTotal = rows.reduce((sum, r) => sum + Number(r.gross_value), 0);
  return (
    <div>
      <h3 className="mb-2 text-[11px] uppercase tracking-widest text-gray-500 font-body">{title}</h3>
      {rows.length === 0 ? (
        <p className="rounded border border-gray-200 bg-white px-3 py-6 text-center text-sm text-gray-400 font-body">
          Nothing in this period.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-gray-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                <th className="px-3 py-2 text-left">Category</th>
                <th className="px-3 py-2 text-right">Net</th>
                <th className="px-3 py-2 text-right">VAT</th>
                <th className="px-3 py-2 text-right">Gross</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.category}-${row.direction}`} className="border-b border-gray-100 last:border-0">
                  <td className="px-3 py-2 font-medium">
                    {categoryLabel(row.category)}
                    {showRecoverable && !row.vat_recoverable && (
                      <Badge variant="warning" className="ml-2">Non-recoverable</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatCurrency(row.net_value)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatCurrency(row.vat_amount)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatCurrency(row.gross_value)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-200 bg-gray-50 text-xs font-medium text-gray-600">
                <td className="px-3 py-2 text-left uppercase tracking-wider">Total</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCurrency(netTotal)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCurrency(vatTotal)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCurrency(grossTotal)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
