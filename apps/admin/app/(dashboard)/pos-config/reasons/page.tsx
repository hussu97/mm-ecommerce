'use client';

import { useCallback } from 'react';
import { reasonsApi } from '@/lib/pos-api';
import type { Reason } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

export default function ReasonsTab() {
  const load = useCallback(() => reasonsApi.list(), []);
  return (
    <ResourcePage<Reason>
      title="Reasons"
      description="Pre-defined justifications. Reporting groups voids and cash movements by these, so they are not free text."
      load={load}
      create={(d) => reasonsApi.create(d as Partial<Reason>)}
      update={(id, d) => reasonsApi.update(id, d as Partial<Reason>)}
      remove={(id) => reasonsApi.remove(id)}
      searchKeys={['name']}
      defaults={{ type: 'void_return', is_active: true }}
      columns={[
        { header: 'Name', priority: 'primary', render: (r) => <span className="font-medium">{r.name}</span> },
        {
          header: 'Used for',
          render: (r) => <span className="capitalize">{r.type.replace(/_/g, ' ')}</span>,
        },
        { header: 'Status', render: (r) => <StatusBadge active={r.is_active && !r.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'name_localized', label: 'Name (Arabic)' },
        {
          name: 'type',
          label: 'Used for',
          type: 'select',
          required: true,
          options: [
            { value: 'void_return', label: 'Voids and returns' },
            { value: 'quantity_adjustment', label: 'Stock adjustments' },
            { value: 'drawer_operation', label: 'Drawer operations' },
          ],
        },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
