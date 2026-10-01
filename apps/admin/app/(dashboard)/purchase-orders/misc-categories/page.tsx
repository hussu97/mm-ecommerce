'use client';

import { useCallback } from 'react';
import { Badge } from '@/components/ui';
import { ResourcePage, StatusBadge, type FieldDef } from '@/components/pos/ResourcePage';
import { useAuth } from '@/lib/auth-context';
import { inventoryApi, type PurchaseOrderMiscCategory } from '@/lib/pos-api';
import {
  canSeeRestrictedMisc,
  PNL_CHANNEL_OPTIONS,
  PNL_LEVEL_OPTIONS,
  pnlPlacementLabel,
} from '@/lib/purchasing';

/**
 * What a misc (non-stock) PO line is for — shared by every supplier. An
 * admin-only category (rent, salary…) never reaches the till, and here only
 * holders of the restricted permission see it or can set the flag; the API
 * returns the list alphabetically either way.
 *
 * The P&L placement says where a line's cost lands: above which profit level,
 * split over the chosen channels by their GMV. A supplier's own placement, when
 * it has one, wins over the category's.
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
    {
      name: 'pnl_level',
      label: 'P&L level',
      type: 'select',
      nullable: true,
      placeholder: 'Default (PC4)',
      options: PNL_LEVEL_OPTIONS,
      helper: 'The profit level this cost is taken off. A supplier’s own placement overrides it.',
    },
    {
      name: 'pnl_channels',
      label: 'P&L channels',
      type: 'multiselect',
      placeholder: 'All channels',
      options: PNL_CHANNEL_OPTIONS,
      helper: 'The cost is split across these channels in proportion to their GMV.',
    },
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
      defaults={{ is_active: true, admin_only: false, pnl_level: '', pnl_channels: [] }}
      emptyMessage="No categories yet."
      columns={[
        { header: 'Name', priority: 'primary', render: (c) => <span className="font-medium">{c.name}</span> },
        {
          header: 'Visibility',
          render: (c) =>
            c.admin_only ? <Badge variant="warning">Admin only</Badge> : 'Admin & POS',
        },
        {
          header: 'P&L placement',
          className: 'whitespace-normal',
          render: (c) => (
            <span className={c.pnl_level || c.pnl_channels.length ? 'text-gray-700' : 'text-gray-400'}>
              {pnlPlacementLabel(c.pnl_level, c.pnl_channels)}
            </span>
          ),
        },
        { header: 'Status', render: (c) => <StatusBadge active={c.is_active && !c.deleted_at} /> },
      ]}
      fields={fields}
    />
  );
}
