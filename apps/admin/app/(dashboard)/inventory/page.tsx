'use client';

import { useCallback, useEffect, useState } from 'react';
import { branchesApi, inventoryApi } from '@/lib/pos-api';
import type {
  Branch,
  InventoryCategory,
  InventoryItem,
  InventoryLevel,
  Supplier,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Select, Spinner, TabBar } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';
import { formatCurrency } from '@/lib/utils';

type TabKey = 'items' | 'levels' | 'suppliers' | 'categories';

export default function InventoryPage() {
  const [tab, setTab] = useState<TabKey>('items');

  return (
    <div>
      <div className="border-b border-gray-200 px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">Inventory</h1>
        <TabBar
          tabs={[
            { key: 'items', label: 'Items' },
            { key: 'levels', label: 'Stock Levels' },
            { key: 'suppliers', label: 'Suppliers' },
            { key: 'categories', label: 'Categories' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as TabKey)}
        />
      </div>
      {tab === 'items' && <ItemsTab />}
      {tab === 'levels' && <LevelsTab />}
      {tab === 'suppliers' && <SuppliersTab />}
      {tab === 'categories' && <CategoriesTab />}
    </div>
  );
}

function ItemsTab() {
  const [categories, setCategories] = useState<InventoryCategory[]>([]);
  useEffect(() => {
    void inventoryApi.categories().then(setCategories).catch(() => setCategories([]));
  }, []);

  const load = useCallback(() => inventoryApi.items(), []);

  return (
    <ResourcePage<InventoryItem>
      title="Inventory Items"
      description="Raw materials and tracked goods. Items are bought in a storage unit and consumed in an ingredient unit."
      // The one ResourcePage list with no natural ceiling — every ingredient
      // ever bought lands here — so it pages where its siblings (a dozen
      // taxes, a handful of branches) do not.
      paginated
      load={load}
      create={(d) => inventoryApi.createItem(d)}
      update={(id, d) => inventoryApi.updateItem(id, d)}
      remove={(id) => inventoryApi.removeItem(id)}
      searchKeys={['name', 'sku']}
      defaults={{
        storage_unit: 'kg',
        ingredient_unit: 'g',
        storage_to_ingredient_factor: 1000,
        minimum_level: 0,
        maximum_level: 0,
        par_level: 0,
        cost: 0,
        costing_method: 'fixed',
        yield_percentage: 1,
        is_product: false,
        is_active: true,
      }}
      emptyMessage="No inventory items yet."
      columns={[
        { header: 'SKU', priority: 'secondary', render: (i) => <code className="text-xs text-gray-500">{i.sku}</code> },
        { header: 'Name', priority: 'primary', render: (i) => <span className="font-medium">{i.name}</span> },
        {
          header: 'Units',
          render: (i) => (
            <span className="text-xs text-gray-600">
              1 {i.storage_unit} = {Number(i.storage_to_ingredient_factor)} {i.ingredient_unit}
            </span>
          ),
        },
        { header: 'Cost', render: (i) => formatCurrency(i.cost) },
        { header: 'Min', render: (i) => Number(i.minimum_level) },
        { header: 'Par', render: (i) => Number(i.par_level) },
        { header: 'Status', render: (i) => <StatusBadge active={i.is_active && !i.deleted_at} /> },
      ]}
      fields={[
        { name: 'sku', label: 'SKU', required: true },
        { name: 'name', label: 'Name', required: true },
        { name: 'barcode', label: 'Barcode' },
        {
          name: 'category_id',
          label: 'Category',
          type: 'select',
          options: categories.map((c) => ({ value: c.id, label: c.name })),
        },
        { name: 'storage_unit', label: 'Storage unit', helper: 'How it is purchased, e.g. kg, box' },
        { name: 'ingredient_unit', label: 'Ingredient unit', helper: 'How it is used, e.g. g, ml' },
        {
          name: 'storage_to_ingredient_factor',
          label: 'Conversion factor',
          type: 'number',
          step: '0.000001',
          helper: '1 storage unit = this many ingredient units',
        },
        { name: 'cost', label: 'Cost per storage unit', type: 'number', step: '0.000001' },
        { name: 'minimum_level', label: 'Minimum level', type: 'number', step: '0.0001' },
        { name: 'par_level', label: 'Par level', type: 'number', step: '0.0001' },
        { name: 'maximum_level', label: 'Maximum level', type: 'number', step: '0.0001' },
        {
          name: 'is_product',
          label: 'Sold directly as a retail item',
          type: 'checkbox',
        },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}

function LevelsTab() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchId, setBranchId] = useState('');
  const [levels, setLevels] = useState<InventoryLevel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [belowOnly, setBelowOnly] = useState(false);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    inventoryApi
      .levels({ branch_id: branchId || undefined, below_minimum_only: belowOnly })
      .then((rows) => {
        if (!cancelled) {
          setLevels(rows);
          setError('');
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load levels.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [branchId, belowOnly]);

  const totalValue = levels.reduce((sum, l) => sum + Number(l.total_value ?? 0), 0);

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Select
          label="Branch"
          value={branchId}
          onChange={(e) => setBranchId(e.target.value)}
          options={branches.map((b) => ({ value: b.id, label: b.name }))}
          placeholder="All branches"
          className="w-56"
        />
        <label className="flex items-center gap-2 pb-2 text-sm font-body">
          <input
            type="checkbox"
            checked={belowOnly}
            onChange={(e) => setBelowOnly(e.target.checked)}
            className="h-4 w-4 accent-[color:var(--color-primary)]"
          />
          Below minimum only
        </label>
        <div className="ml-auto pb-2 text-right">
          <p className="text-[11px] uppercase tracking-widest text-gray-400 font-body">
            Stock value
          </p>
          <p className="font-display text-lg text-primary">{formatCurrency(totalValue)}</p>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : levels.length === 0 ? (
        <p className="py-16 text-center text-sm text-gray-400 font-body">
          No stock recorded yet. Receive a purchase order to get started.
        </p>
      ) : (
        <DataTable<InventoryLevel>
          rows={levels}
          rowKey={(l) => l.id}
          columns={[
            { header: 'Item', priority: 'primary', render: (l) => l.item_name },
            {
              header: 'SKU',
              priority: 'secondary',
              render: (l) => <code className="text-xs text-gray-500">{l.item_sku}</code>,
            },
            {
              header: 'On hand',
              className: 'text-right',
              render: (l) => (
                <>
                  {Number(l.quantity)}{' '}
                  <span className="text-xs text-gray-400">{l.ingredient_unit}</span>
                </>
              ),
            },
            {
              header: 'Min',
              className: 'text-right',
              render: (l) => <span className="text-gray-500">{Number(l.minimum_level ?? 0)}</span>,
            },
            {
              header: 'Par',
              className: 'text-right',
              render: (l) => <span className="text-gray-500">{Number(l.par_level ?? 0)}</span>,
            },
            {
              header: 'Avg cost',
              className: 'text-right',
              render: (l) => Number(l.average_cost).toFixed(4),
            },
            {
              header: 'Value',
              className: 'text-right',
              render: (l) => formatCurrency(l.total_value ?? 0),
            },
            {
              header: 'Flag',
              render: (l) => (l.is_below_minimum ? <Badge variant="danger">Reorder</Badge> : '—'),
            },
          ]}
        />
      )}
    </div>
  );
}

function SuppliersTab() {
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
        { header: 'Name', priority: 'primary', render: (s) => <span className="font-medium">{s.name}</span> },
        { header: 'Contact', render: (s) => s.contact_name ?? '—' },
        { header: 'Phone', render: (s) => s.phone ?? '—' },
        { header: 'Email', priority: 'secondary', render: (s) => <span className="text-xs">{s.email ?? '—'}</span> },
        { header: 'Terms', render: (s) => `${s.payment_terms_days} days` },
        { header: 'Status', render: (s) => <StatusBadge active={s.is_active && !s.deleted_at} /> },
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

function CategoriesTab() {
  const load = useCallback(() => inventoryApi.categories(), []);
  return (
    <ResourcePage<InventoryCategory>
      title="Inventory Categories"
      load={load}
      create={(d) => inventoryApi.createCategory(d)}
      update={(id, d) => inventoryApi.updateCategory(id, d)}
      remove={(id) => inventoryApi.removeCategory(id)}
      searchKeys={['name']}
      defaults={{ display_order: 0, is_active: true }}
      emptyMessage="No categories yet."
      columns={[
        { header: 'Name', priority: 'primary', render: (c) => <span className="font-medium">{c.name}</span> },
        { header: 'Reference', priority: 'secondary', render: (c) => c.reference ?? '—' },
        { header: 'Order', render: (c) => c.display_order },
        { header: 'Status', render: (c) => <StatusBadge active={c.is_active && !c.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'reference', label: 'Reference' },
        { name: 'display_order', label: 'Display order', type: 'number' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
