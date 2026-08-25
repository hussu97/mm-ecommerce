'use client';

import { useState } from 'react';
import { posReportsApi } from '@/lib/pos-api';
import { Select } from '@/components/ui';
import { formatCurrency } from '@/lib/utils';
import { useReport, windowKey, Panel, Stat, BreakdownTable } from './_shared';
import type { SalesBreakdownRow, SalesSummary } from '@/lib/pos-types';
import type { Window } from '../report-window';


export function SalesTab({ window }: { window: Window }) {
  const [dimension, setDimension] = useState('product');
  const summary = useReport<SalesSummary>(
    () => posReportsApi.salesSummary(window),
    windowKey(window),
  );
  const breakdown = useReport<SalesBreakdownRow[]>(
    () => posReportsApi.salesBy(dimension, window),
    windowKey(window, dimension),
  );

  return (
    <div className="space-y-6">
      <Panel loading={summary.loading} error={summary.error}>
        {summary.data && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Net sales" value={formatCurrency(summary.data.net_sales)} />
            <Stat label="Orders" value={String(summary.data.orders_count)} />
            <Stat label="Average order" value={formatCurrency(summary.data.average_order_value)} />
            <Stat label="VAT collected" value={formatCurrency(summary.data.taxes)} />
            <Stat label="Discounts" value={formatCurrency(summary.data.discounts)} />
            <Stat label="Charges" value={formatCurrency(summary.data.charges)} />
            <Stat label="Returns" value={formatCurrency(summary.data.returns)} />
            <Stat
              label="Voided"
              value={formatCurrency(summary.data.voided_value)}
              hint={`${summary.data.voided_orders} order(s)`}
            />
          </div>
        )}
      </Panel>

      <div>
        <div className="mb-3 flex items-end gap-3">
          <Select
            label="Break down by"
            value={dimension}
            onChange={(e) => setDimension(e.target.value)}
            options={[
              { value: 'product', label: 'Product' },
              { value: 'category', label: 'Category' },
              { value: 'modifier_option', label: 'Modifier option' },
              { value: 'product_tag', label: 'Product tag' },
              { value: 'order_tag', label: 'Order tag' },
              { value: 'source', label: 'Order source' },
              { value: 'business_date', label: 'Day' },
              { value: 'hour', label: 'Hour of day' },
              { value: 'branch', label: 'Branch' },
              { value: 'section', label: 'Section' },
              { value: 'table', label: 'Table' },
              { value: 'revenue_center', label: 'Revenue centre' },
              { value: 'cashier', label: 'Cashier' },
              { value: 'creator', label: 'Created by' },
              { value: 'driver', label: 'Driver' },
              { value: 'customer', label: 'Customer' },
              { value: 'discount', label: 'Discount' },
              { value: 'coupon', label: 'Coupon' },
              { value: 'promotion', label: 'Promotion' },
              { value: 'timed_event', label: 'Timed event' },
              { value: 'charge', label: 'Charge' },
              { value: 'tax', label: 'Tax' },
            ]}
            className="w-52"
          />
        </div>
        <Panel
          loading={breakdown.loading}
          error={breakdown.error}
          empty={!breakdown.data?.length}
        >
          {breakdown.data && <BreakdownTable rows={breakdown.data} unitLabel="Name" />}
        </Panel>
      </div>
    </div>
  );
}
