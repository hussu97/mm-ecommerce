'use client';

import { useCallback } from 'react';
import { Badge } from '@/components/ui';
import { ResourcePage } from '@/components/pos/ResourcePage';
import { inventoryApi, type PurchaseOrderMiscPeriod } from '@/lib/pos-api';
import { periodLabel } from '@/lib/purchasing';

const UNIT_LABEL: Record<string, [string, string]> = {
  day: ['day', 'days'],
  week: ['week', 'weeks'],
  month: ['month', 'months'],
};

function span(p: PurchaseOrderMiscPeriod): string {
  const [one, many] = UNIT_LABEL[p.unit] ?? [p.unit, p.unit];
  return `${p.length} ${p.length === 1 ? one : many}`;
}

/**
 * The presets a misc line's period picker offers. A preset is a unit and a
 * length: it decides how the picker asks (months for `month`) and what it
 * pre-fills — the current block, so 3 months is this quarter and 12 this
 * year. Lines store their own dates; editing a preset never moves them.
 */
export default function MiscPeriodsPage() {
  const load = useCallback(() => inventoryApi.miscPeriods(), []);
  return (
    <ResourcePage<PurchaseOrderMiscPeriod>
      title="Misc periods"
      description="Presets for the period a miscellaneous line covers. The P&L spreads each line's cost equally over its days."
      load={load}
      create={(d) => inventoryApi.createMiscPeriod(d)}
      update={(id, d) => inventoryApi.updateMiscPeriod(id, d)}
      remove={(id) => inventoryApi.removeMiscPeriod(id)}
      searchKeys={['name']}
      defaults={{ unit: 'month', length: 1, is_default: false, display_order: 0 }}
      emptyMessage="No period presets yet."
      columns={[
        {
          header: 'Name',
          priority: 'primary',
          render: (p) => (
            <span className="font-medium">
              {p.name}
              {p.is_default && (
                <Badge variant="info" className="ml-2">
                  Default
                </Badge>
              )}
            </span>
          ),
        },
        { header: 'Length', render: span },
        { header: 'Pre-fills today', render: (p) => periodLabel(p.default_from, p.default_to) },
        { header: 'Order', render: (p) => p.display_order },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        {
          name: 'unit',
          label: 'Unit',
          type: 'select',
          required: true,
          options: [
            { value: 'day', label: 'Days' },
            { value: 'week', label: 'Weeks (Monday–Sunday)' },
            { value: 'month', label: 'Months' },
          ],
          helper: 'How the picker asks for the range.',
        },
        {
          name: 'length',
          label: 'Length',
          type: 'number',
          required: true,
          helper: 'Month blocks count from January: 3 = this quarter, 12 = this year.',
        },
        { name: 'is_default', label: 'Default for new lines', type: 'checkbox' },
        { name: 'display_order', label: 'Display order', type: 'number' },
      ]}
    />
  );
}
