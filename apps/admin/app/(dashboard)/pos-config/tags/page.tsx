'use client';

import { useCallback } from 'react';
import { tagsApi } from '@/lib/pos-api';
import type { Tag } from '@/lib/pos-types';
import { ResourcePage } from '@/components/pos/ResourcePage';

export default function TagsTab() {
  const load = useCallback(() => tagsApi.list(), []);
  return (
    <ResourcePage<Tag>
      title="Tags"
      description="Labels for orders, customers, products and revenue centres."
      load={load}
      create={(d) => tagsApi.create(d as Partial<Tag>)}
      update={(id, d) => tagsApi.update(id, d as Partial<Tag>)}
      remove={(id) => tagsApi.remove(id)}
      searchKeys={['name']}
      defaults={{ type: 'order' }}
      columns={[
        {
          header: 'Name',
          render: (t) => (
            <span className="flex items-center gap-2 font-medium">
              {t.color && (
                <span
                  className="inline-block h-3 w-3 rounded-full border border-gray-200"
                  style={{ backgroundColor: t.color }}
                />
              )}
              {t.name}
            </span>
          ),
        },
        { header: 'Type', render: (t) => <span className="capitalize">{t.type.replace(/_/g, ' ')}</span> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        {
          name: 'type',
          label: 'Type',
          type: 'select',
          required: true,
          options: [
            { value: 'order', label: 'Order' },
            { value: 'customer', label: 'Customer' },
            { value: 'product', label: 'Product' },
            { value: 'inventory_item', label: 'Inventory item' },
            { value: 'revenue_center', label: 'Revenue centre' },
          ],
        },
        { name: 'color', label: 'Colour', placeholder: '#8A5A64' },
      ]}
    />
  );
}
