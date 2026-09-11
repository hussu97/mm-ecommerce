'use client';

import { useCallback } from 'react';
import { paymentMethodsApi } from '@/lib/pos-api';
import type { PaymentMethod } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

export default function PaymentMethodsTab() {
  const load = useCallback(() => paymentMethodsApi.list(), []);
  return (
    <ResourcePage<PaymentMethod>
      title="Payment Methods"
      description="Tender types the cashier can settle with. Cash methods count toward the till and can kick the drawer."
      load={load}
      create={(d) => paymentMethodsApi.create(d as Partial<PaymentMethod>)}
      update={(id, d) => paymentMethodsApi.update(id, d as Partial<PaymentMethod>)}
      remove={(id) => paymentMethodsApi.remove(id)}
      searchKeys={['name', 'code']}
      defaults={{ type: 'cash', is_active: true, allows_refund: true, display_order: 0 }}
      columns={[
        { header: 'Name', priority: 'primary', render: (m) => <span className="font-medium">{m.name}</span> },
        { header: 'Code', priority: 'secondary', render: (m) => <code className="text-xs text-gray-500">{m.code}</code> },
        { header: 'Type', render: (m) => <span className="capitalize">{m.type.replace('_', ' ')}</span> },
        { header: 'Opens drawer', render: (m) => (m.auto_open_drawer ? 'Yes' : '—') },
        { header: 'Tips', render: (m) => (m.allows_tips ? 'Yes' : '—') },
        { header: 'Status', render: (m) => <StatusBadge active={m.is_active && !m.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        {
          name: 'code',
          label: 'Code',
          required: true,
          helper: 'Lowercase, letters/numbers/underscore',
        },
        {
          name: 'type',
          label: 'Type',
          type: 'select',
          required: true,
          options: [
            { value: 'cash', label: 'Cash' },
            { value: 'card', label: 'Card' },
            { value: 'online', label: 'Online' },
            { value: 'other', label: 'Other' },
          ],
        },
        { name: 'auto_open_drawer', label: 'Opens the cash drawer', type: 'checkbox' },
        {
          name: 'allows_tendering',
          label: 'Show quick-cash pad',
          type: 'checkbox',
          helper: 'Lets the cashier key what the customer handed over',
        },
        { name: 'allows_tips', label: 'Accepts tips', type: 'checkbox' },
        { name: 'allows_refund', label: 'Can be refunded', type: 'checkbox' },
        { name: 'display_order', label: 'Display order', type: 'number' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
