'use client';

import { useCallback, useState } from 'react';
import {
  chargesApi,
  paymentMethodsApi,
  reasonsApi,
  tagsApi,
  taxesApi,
} from '@/lib/pos-api';
import type { Charge, PaymentMethod, Reason, Tag, Tax } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';
import { TabBar } from '@/components/ui';

type TabKey = 'payment-methods' | 'taxes' | 'charges' | 'reasons' | 'tags';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'payment-methods', label: 'Payment Methods' },
  { key: 'taxes', label: 'Taxes' },
  { key: 'charges', label: 'Charges' },
  { key: 'reasons', label: 'Reasons' },
  { key: 'tags', label: 'Tags' },
];

const ORDER_TYPE_OPTIONS = [
  { value: 'dine_in', label: 'Dine In' },
  { value: 'pickup', label: 'Takeaway' },
  { value: 'delivery', label: 'Delivery' },
  { value: 'drive_thru', label: 'Drive Thru' },
];

export default function PosConfigPage() {
  const [tab, setTab] = useState<TabKey>('payment-methods');

  return (
    <div>
      <div className="border-b border-gray-200 px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">POS Configuration</h1>
        <TabBar
          tabs={TABS.map((t) => ({ key: t.key, label: t.label }))}
          active={tab}
          onChange={(k) => setTab(k as TabKey)}
        />
      </div>
      {tab === 'payment-methods' && <PaymentMethodsTab />}
      {tab === 'taxes' && <TaxesTab />}
      {tab === 'charges' && <ChargesTab />}
      {tab === 'reasons' && <ReasonsTab />}
      {tab === 'tags' && <TagsTab />}
    </div>
  );
}

function PaymentMethodsTab() {
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

function TaxesTab() {
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
        { header: 'Name', priority: 'primary', render: (t) => <span className="font-medium">{t.name}</span> },
        { header: 'Rate', render: (t) => `${(Number(t.rate) * 100).toFixed(2)}%` },
        { header: 'Type', render: (t) => <span className="capitalize">{t.type}</span> },
        { header: 'Status', render: (t) => <StatusBadge active={t.is_active && !t.deleted_at} /> },
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

function ChargesTab() {
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
        { header: 'Name', priority: 'primary', render: (c) => <span className="font-medium">{c.name}</span> },
        { header: 'Type', render: (c) => <span className="capitalize">{c.type}</span> },
        {
          header: 'Value',
          render: (c) => (c.type === 'percentage' ? `${(Number(c.value) * 100).toFixed(2)}%` : Number(c.value).toFixed(2)),
        },
        { header: 'Auto', render: (c) => (c.is_auto_applied ? 'Yes' : '—') },
        {
          header: 'Order types',
          render: (c) => (c.order_types.length ? c.order_types.join(', ') : 'All'),
        },
        { header: 'Status', render: (c) => <StatusBadge active={c.is_active && !c.deleted_at} /> },
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

function ReasonsTab() {
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

function TagsTab() {
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

export { ORDER_TYPE_OPTIONS };
