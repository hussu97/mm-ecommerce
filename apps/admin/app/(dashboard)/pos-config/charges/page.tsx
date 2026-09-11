'use client';

import { useCallback } from 'react';
import { chargesApi } from '@/lib/pos-api';
import type { Charge } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

export default function ChargesTab() {
  const load = useCallback(() => chargesApi.list(), []);
  return (
    <ResourcePage<Charge>
      title="Charges"
      description="Delivery fees, service charges and packaging. Percentage charges are fractions of the net sale."
      load={load}
      create={(d) => chargesApi.create(d as Partial<Charge>)}
      update={(id, d) => chargesApi.update(id, d as Partial<Charge>)}
      remove={(id) => chargesApi.remove(id)}
      searchKeys={['name']}
      defaults={{ type: 'fixed', value: 0, is_auto_applied: false, is_active: true, order_types: [] }}
      columns={[
        { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (c) => c.name, render: (c) => <span className="font-medium">{c.name}</span> },
        { header: 'Type', sortable: true, sortAccessor: (c) => c.type, render: (c) => <span className="capitalize">{c.type}</span> },
        {
          header: 'Value',
          sortable: true,
          sortAccessor: (c) => Number(c.value),
          render: (c) => (c.type === 'percentage' ? `${(Number(c.value) * 100).toFixed(2)}%` : Number(c.value).toFixed(2)),
        },
        { header: 'Auto', sortable: true, sortAccessor: (c) => (c.is_auto_applied ? 'Yes' : '—'), render: (c) => (c.is_auto_applied ? 'Yes' : '—') },
        {
          header: 'Order types',
          sortable: true,
          sortAccessor: (c) => (c.order_types.length ? c.order_types.join(', ') : 'All'),
          render: (c) => (c.order_types.length ? c.order_types.join(', ') : 'All'),
        },
        { header: 'Status', sortable: true, sortAccessor: (c) => (c.is_active && !c.deleted_at ? 'Active' : 'Inactive'), render: (c) => <StatusBadge active={c.is_active && !c.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        {
          name: 'type',
          label: 'Type',
          type: 'select',
          required: true,
          options: [
            { value: 'fixed', label: 'Fixed amount' },
            { value: 'percentage', label: 'Percentage of net sale' },
            { value: 'open', label: 'Open — cashier keys the amount' },
          ],
        },
        { name: 'value', label: 'Value', type: 'number', step: '0.0001' },
        { name: 'is_auto_applied', label: 'Apply automatically', type: 'checkbox' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
