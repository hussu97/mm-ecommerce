'use client';

import { useCallback } from 'react';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

export default function BranchesPage() {
  const load = useCallback(() => branchesApi.list(), []);

  return (
    <ResourcePage<Branch>
      title="Branches"
      description="Shops, production kitchens and warehouses. Every order, till and stock level belongs to one."
      load={load}
      create={(d) => branchesApi.create(d as Partial<Branch>)}
      update={(id, d) => branchesApi.update(id, d as Partial<Branch>)}
      remove={(id) => branchesApi.remove(id)}
      searchKeys={['name', 'reference']}
      emptyMessage="No branches yet. Create one to start using the POS."
      defaults={{
        type: 'restaurant',
        opening_from: '09:00',
        opening_to: '23:00',
        business_day_start: '04:00',
        receives_online_orders: true,
        accepts_reservations: false,
        is_active: true,
        display_order: 0,
      }}
      columns={[
        { header: 'Name', render: (b) => <span className="font-medium">{b.name}</span> },
        { header: 'Reference', render: (b) => <code className="text-xs text-gray-500">{b.reference}</code> },
        { header: 'Type', render: (b) => <span className="capitalize">{b.type}</span> },
        { header: 'Hours', render: (b) => `${b.opening_from} – ${b.opening_to}` },
        { header: 'Day starts', render: (b) => b.business_day_start },
        { header: 'Online', render: (b) => (b.receives_online_orders ? 'Yes' : 'No') },
        {
          header: 'noon Send',
          render: (b) =>
            b.noon_send_outlet_code ? (
              <code className="text-xs text-gray-500">{b.noon_send_outlet_code}</code>
            ) : (
              <span className="text-xs text-gray-400">—</span>
            ),
        },
        { header: 'Status', render: (b) => <StatusBadge active={b.is_active && !b.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'name_localized', label: 'Name (Arabic)' },
        {
          name: 'reference',
          label: 'Reference',
          required: true,
          helper: 'Short unique code, printed on order numbers',
        },
        {
          name: 'type',
          label: 'Type',
          type: 'select',
          options: [
            { value: 'restaurant', label: 'Restaurant / shop' },
            { value: 'kitchen', label: 'Production kitchen' },
            { value: 'warehouse', label: 'Warehouse' },
          ],
        },
        { name: 'phone', label: 'Phone' },
        { name: 'address', label: 'Address', type: 'textarea' },
        { name: 'opening_from', label: 'Opens', placeholder: '09:00' },
        { name: 'opening_to', label: 'Closes', placeholder: '23:00' },
        {
          name: 'business_day_start',
          label: 'Trading day starts',
          placeholder: '04:00',
          helper: 'Sales before this time count toward the previous day',
        },
        { name: 'tax_number', label: 'Tax registration number (TRN)' },
        { name: 'tax_registration_name', label: 'Tax registration name' },
        { name: 'receipt_header', label: 'Receipt header', type: 'textarea' },
        { name: 'receipt_footer', label: 'Receipt footer', type: 'textarea' },
        {
          name: 'noon_send_outlet_code',
          label: 'noon Send outlet code',
          placeholder: 'PCKP_... or CMFRTF2DXS',
          helper:
            'From registering this branch with noon Send. Leave empty and its orders go by Lalamove instead.',
        },
        {
          name: 'noon_send_outlet_address_code',
          label: 'noon Send outlet address code',
          placeholder: 'addr::restaurant_outlet::ae::CODE::2',
          helper:
            'Optional. Pins the outlet address revision. The trailing number changes whenever noon edits the address, so leave it empty rather than let it go stale.',
        },
        { name: 'receives_online_orders', label: 'Accepts online orders', type: 'checkbox' },
        { name: 'accepts_reservations', label: 'Accepts reservations', type: 'checkbox' },
        { name: 'display_order', label: 'Display order', type: 'number' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
