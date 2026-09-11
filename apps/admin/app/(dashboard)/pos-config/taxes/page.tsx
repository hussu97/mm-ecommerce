'use client';

import { useCallback } from 'react';
import { taxesApi } from '@/lib/pos-api';
import type { Tax } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

export default function TaxesTab() {
  const load = useCallback(() => taxesApi.list(), []);
  return (
    <ResourcePage<Tax>
      title="Taxes"
      description="Rates are fractions — enter 0.05 for 5%. Inclusive means the menu price already contains the tax."
      load={load}
      create={(d) => taxesApi.create(d as Partial<Tax>)}
      update={(id, d) => taxesApi.update(id, d as Partial<Tax>)}
      remove={(id) => taxesApi.remove(id)}
      searchKeys={['name']}
      defaults={{ type: 'inclusive', is_active: true, rate: 0.05 }}
      columns={[
        { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (t) => t.name, render: (t) => <span className="font-medium">{t.name}</span> },
        { header: 'Rate', sortable: true, sortAccessor: (t) => Number(t.rate), render: (t) => `${(Number(t.rate) * 100).toFixed(2)}%` },
        { header: 'Type', sortable: true, sortAccessor: (t) => t.type, render: (t) => <span className="capitalize">{t.type}</span> },
        { header: 'Status', sortable: true, sortAccessor: (t) => (t.is_active && !t.deleted_at ? 'Active' : 'Inactive'), render: (t) => <StatusBadge active={t.is_active && !t.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'name_localized', label: 'Name (Arabic)' },
        { name: 'rate', label: 'Rate (fraction)', type: 'number', step: '0.0001', required: true },
        {
          name: 'type',
          label: 'Type',
          type: 'select',
          options: [
            { value: 'inclusive', label: 'Inclusive — price includes tax' },
            { value: 'exclusive', label: 'Exclusive — tax added at checkout' },
          ],
        },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
