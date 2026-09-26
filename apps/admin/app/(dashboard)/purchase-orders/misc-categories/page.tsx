'use client';

import { useCallback } from 'react';
import { Badge } from '@/components/ui';
import { ResourcePage, StatusBadge, type FieldDef } from '@/components/pos/ResourcePage';
import { useAuth } from '@/lib/auth-context';
import { inventoryApi, type PurchaseOrderMiscCategory } from '@/lib/pos-api';
import { canSeeRestrictedMisc } from '@/lib/purchasing';

/**
 * What a misc (non-stock) PO line is for — shared by every supplier. An
 * admin-only category (rent, salary…) never reaches the till, and here only
 * holders of the restricted permission see it or can set the flag; the API
 * returns the list alphabetically either way.
 */
export default function MiscCategoriesPage() {
  const { user } = useAuth();
  const seesRestricted = canSeeRestrictedMisc(user);
  const load = useCallback(() => inventoryApi.miscCategories(), []);
  const fields: FieldDef[] = [
    { name: 'name', label: 'Name', required: true },
    ...(seesRestricted
      ? [
          {
            name: 'admin_only',
            label: 'Admin only (hidden from POS)',
            type: 'checkbox' as const,
            helper: 'Lines in this category never show on the till, even on received orders.',
          },
        ]
      : []),
    { name: 'is_active', label: 'Active', type: 'checkbox' },
  ];
  return (
    <ResourcePage<PurchaseOrderMiscCategory>
      title="Misc categories"
      description="Every miscellaneous purchase-order line is filed under one of these."
      load={load}
      create={(d) => inventoryApi.createMiscCategory(d)}
      update={(id, d) => inventoryApi.updateMiscCategory(id, d)}
      remove={(id) => inventoryApi.removeMiscCategory(id)}
      searchKeys={['name']}
      defaults={{ is_active: true, admin_only: false }}
      emptyMessage="No categories yet."
      columns={[
        { header: 'Name', priority: 'primary', render: (c) => <span className="font-medium">{c.name}</span> },
        {
          header: 'Visibility',
          render: (c) =>
            c.admin_only ? <Badge variant="warning">Admin only</Badge> : 'Admin & POS',
        },
        { header: 'Status', render: (c) => <StatusBadge active={c.is_active && !c.deleted_at} /> },
      ]}
      fields={fields}
    />
  );
}
