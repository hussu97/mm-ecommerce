'use client';

import { useCallback } from 'react';
import { inventoryApi } from '@/lib/pos-api';
import type { Supplier } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

export default function SuppliersPage() {
  const load = useCallback(() => inventoryApi.suppliers(), []);
  return (
    <ResourcePage<Supplier>
      title="Suppliers"
      load={load}
      create={(d) => inventoryApi.createSupplier(d)}
      update={(id, d) => inventoryApi.updateSupplier(id, d)}
      remove={(id) => inventoryApi.removeSupplier(id)}
      searchKeys={['name']}
      defaults={{ payment_terms_days: 0, is_active: true }}
      emptyMessage="No suppliers yet."
      columns={[
        { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (s) => s.name, render: (s) => <span className="font-medium">{s.name}</span> },
        { header: 'Contact', sortable: true, sortAccessor: (s) => s.contact_name ?? null, render: (s) => s.contact_name ?? '—' },
        { header: 'Phone', sortable: true, sortAccessor: (s) => s.phone ?? null, render: (s) => s.phone ?? '—' },
        { header: 'Email', priority: 'secondary', sortable: true, sortAccessor: (s) => s.email ?? null, render: (s) => <span className="text-xs">{s.email ?? '—'}</span> },
        { header: 'Terms', sortable: true, sortAccessor: (s) => s.payment_terms_days, render: (s) => `${s.payment_terms_days} days` },
        { header: 'Status', sortable: true, sortAccessor: (s) => (s.is_active && !s.deleted_at ? 'Active' : 'Inactive'), render: (s) => <StatusBadge active={s.is_active && !s.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'reference', label: 'Reference' },
        { name: 'contact_name', label: 'Contact name' },
        { name: 'phone', label: 'Phone' },
        { name: 'email', label: 'Email' },
        { name: 'address', label: 'Address', type: 'textarea' },
        { name: 'tax_number', label: 'Tax number' },
        { name: 'payment_terms_days', label: 'Payment terms (days)', type: 'number' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
