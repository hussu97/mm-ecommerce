'use client';

// Items — now with on-hand stock folded in (F4). The separate "On hand" tab is
// gone; instead each item carries one column per active branch showing its
// on-hand quantity there, its single-value columns (including the per-branch
// stock columns) are sortable from their headers, and a Stock filter surfaces
// low / below-par items across the whole estate. The item CRUD and the
// expandable RecipeEditor are unchanged.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  branchesApi,
  inventoryApi,
} from '@/lib/pos-api';
import type { Branch, InventoryCategory, InventoryItem, InventoryLevel } from '@/lib/pos-types';
import { Badge } from '@/components/ui';
import { ResourcePage, StatusBadge, type ColumnDef } from '@/components/pos/ResourcePage';
import { formatCurrency, formatQuantity } from '@/lib/utils';
import { RecipeEditor } from '@/components/inventory/RecipeEditor';

type BranchStock = { quantity: number; is_below_minimum: boolean };
type StockPivot = Map<string, Map<string, BranchStock>>;

export default function ItemsPage() {
  const [categories, setCategories] = useState<InventoryCategory[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [pivot, setPivot] = useState<StockPivot>(new Map());
  const [recipeItem, setRecipeItem] = useState<InventoryItem | null>(null);
  const [categoryId, setCategoryId] = useState('');
  const [kind, setKind] = useState('');
  const [trackingMode, setTrackingMode] = useState('');
  const [status, setStatus] = useState<'active' | 'inactive' | 'all'>('active');
  const [stockFilter, setStockFilter] = useState<'' | 'low' | 'below_par'>('');

  useEffect(() => {
    void inventoryApi.categories().then(setCategories).catch(() => setCategories([]));
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  // Every item×branch level across the estate (no branch filter), pivoted to
  // item → branch → { quantity, below-minimum } so each item row can show its
  // stock at each branch and the filters/sort can reason across branches.
  useEffect(() => {
    void inventoryApi.levels({ limit: 5000 })
      .then((levels: InventoryLevel[]) => {
        const next: StockPivot = new Map();
        for (const level of levels) {
          if (!level.branch_id) continue;
          let byBranch = next.get(level.item_id);
          if (!byBranch) { byBranch = new Map(); next.set(level.item_id, byBranch); }
          byBranch.set(level.branch_id, {
            quantity: Number(level.quantity),
            is_below_minimum: level.is_below_minimum,
          });
        }
        setPivot(next);
      })
      .catch(() => setPivot(new Map()));
  }, []);

  const activeBranches = useMemo(
    () => branches
      .filter((b) => b.is_active && !b.deleted_at)
      .sort((a, b) => a.display_order - b.display_order || a.name.localeCompare(b.name)),
    [branches],
  );

  const categoryNames = useMemo(
    () => new Map(categories.map((category) => [category.id, category.name])),
    [categories],
  );

  const stockOf = useCallback(
    (itemId: string, branchId: string): BranchStock | undefined => pivot.get(itemId)?.get(branchId),
    [pivot],
  );

  const filterRows = useCallback((item: InventoryItem) => {
    if (categoryId && item.category_id !== categoryId) return false;
    if (kind && item.kind !== kind) return false;
    if (trackingMode && item.tracking_mode !== trackingMode) return false;
    const active = item.is_active && !item.deleted_at;
    if (!(status === 'all' || (status === 'active' ? active : !active))) return false;
    if (stockFilter) {
      const byBranch = pivot.get(item.id);
      if (!byBranch) return false;
      const rows = Array.from(byBranch.values());
      // "At least one branch matches" is the rule for both.
      if (stockFilter === 'low') {
        if (!rows.some((r) => r.is_below_minimum)) return false;
      } else {
        const par = Number(item.par_level);
        if (!(par > 0 && rows.some((r) => r.quantity < par))) return false;
      }
    }
    return true;
  }, [categoryId, kind, status, trackingMode, stockFilter, pivot]);

  // The management screen is the one place that shows inactive items (it has an
  // active/inactive/all filter), so it opts into them explicitly; everywhere else
  // gets active-only by default.
  const load = useCallback(() => inventoryApi.items({ include_inactive: true }), []);

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Filter inventory category" value={categoryId} onChange={(event) => setCategoryId(event.target.value)} className="h-9 rounded border border-gray-300 bg-white px-2 text-xs text-gray-700">
        <option value="">All categories</option>
        {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
      </select>
      <select aria-label="Filter inventory kind" value={kind} onChange={(event) => setKind(event.target.value)} className="h-9 rounded border border-gray-300 bg-white px-2 text-xs text-gray-700">
        <option value="">All kinds</option>
        <option value="raw_material">Raw material</option><option value="packaging">Packaging</option><option value="semi_finished">Semi-finished</option><option value="produced_good">Produced good</option><option value="resale_good">Resale good</option>
      </select>
      <select aria-label="Filter inventory tracking" value={trackingMode} onChange={(event) => setTrackingMode(event.target.value)} className="h-9 rounded border border-gray-300 bg-white px-2 text-xs text-gray-700">
        <option value="">All tracking</option><option value="stocked">Stocked</option><option value="phantom">Phantom</option>
      </select>
      <select aria-label="Filter inventory stock" value={stockFilter} onChange={(event) => setStockFilter(event.target.value as typeof stockFilter)} className="h-9 rounded border border-gray-300 bg-white px-2 text-xs text-gray-700">
        <option value="">All stock</option><option value="low">Low stock</option><option value="below_par">Below par</option>
      </select>
      <select aria-label="Filter inventory status" value={status} onChange={(event) => setStatus(event.target.value as typeof status)} className="h-9 rounded border border-gray-300 bg-white px-2 text-xs text-gray-700">
        <option value="active">Active</option><option value="inactive">Inactive</option><option value="all">All statuses</option>
      </select>
    </div>
  );

  const branchColumns: ColumnDef<InventoryItem>[] = activeBranches.map((b) => ({
    header: b.name,
    className: 'text-right whitespace-nowrap',
    sortable: true,
    // Two branches can share a name; key the sort by the stable branch id.
    sortKey: `stock:${b.id}`,
    sortAccessor: (item: InventoryItem) => stockOf(item.id, b.id)?.quantity ?? null,
    render: (item: InventoryItem) => {
      const cell = stockOf(item.id, b.id);
      if (!cell) return <span className="text-gray-300">—</span>;
      return (
        <span className={cell.is_below_minimum ? 'text-red-600 font-medium' : 'text-gray-700'}>
          {formatQuantity(cell.quantity)}
        </span>
      );
    },
  }));

  return (
    <ResourcePage<InventoryItem>
      title="Inventory Items"
      description="Raw materials and tracked goods. Each item is bought in a storage unit and consumed in an ingredient unit; stock columns show the on-hand quantity at each branch."
      paginated
      load={load}
      create={(d) => inventoryApi.createItem(d)}
      update={(id, d) => inventoryApi.updateItem(id, d)}
      remove={(id) => inventoryApi.removeItem(id)}
      searchKeys={['name', 'sku']}
      toolbar={toolbar}
      filterRows={filterRows}
      rowActions={(item) => <button className="text-xs text-primary hover:underline" onClick={() => setRecipeItem((current) => (current?.id === item.id ? null : item))}>{recipeItem?.id === item.id ? 'Close recipe' : 'Recipe'}</button>}
      expandedRow={(item) => (item.id === recipeItem?.id ? <div className="py-2"><RecipeEditor ownerKind="inventory_item" ownerId={item.id} ownerLabel={item.name} /></div> : null)}
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
        kind: 'raw_material',
        tracking_mode: 'stocked',
        count_order: 0,
        is_active: true,
      }}
      emptyMessage="No inventory items yet."
      columns={[
        { header: 'SKU', priority: 'secondary', sortable: true, sortAccessor: (i) => i.sku, render: (i) => <code className="text-xs text-gray-500">{i.sku}</code> },
        { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (i) => i.name, render: (i) => <span className="font-medium">{i.name}</span> },
        {
          header: 'Category',
          priority: 'secondary',
          sortable: true,
          sortAccessor: (i) => categoryNames.get(i.category_id ?? '') ?? 'Uncategorised',
          render: (i) => categoryNames.get(i.category_id ?? '') ?? <span className="text-gray-400">Uncategorised</span>,
        },
        {
          header: 'Units',
          render: (i) => (
            <span className="text-xs text-gray-600">
              1 {i.storage_unit} = {Number(i.storage_to_ingredient_factor)} {i.ingredient_unit}
            </span>
          ),
        },
        { header: 'Cost', render: (i) => formatCurrency(i.cost) },
        { header: 'Kind', sortable: true, sortAccessor: (i) => i.kind, render: (i) => <Badge>{i.kind.replaceAll('_', ' ')}</Badge> },
        { header: 'Tracking', render: (i) => <Badge variant={i.tracking_mode === 'phantom' ? 'warning' : 'neutral'}>{i.tracking_mode}</Badge> },
        { header: 'Min', className: 'text-right', sortable: true, sortAccessor: (i) => Number(i.minimum_level), render: (i) => Number(i.minimum_level) },
        { header: 'Par', className: 'text-right', sortable: true, sortAccessor: (i) => Number(i.par_level), render: (i) => Number(i.par_level) },
        ...branchColumns,
        { header: 'Status', sortable: true, sortAccessor: (i) => (i.is_active && !i.deleted_at ? 'Active' : 'Inactive'), render: (i) => <StatusBadge active={i.is_active && !i.deleted_at} /> },
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
        {
          name: 'kind', label: 'Item kind', type: 'select', options: [
            { value: 'raw_material', label: 'Raw material' },
            { value: 'packaging', label: 'Packaging' },
            { value: 'semi_finished', label: 'Semi-finished' },
            { value: 'produced_good', label: 'Produced good' },
            { value: 'resale_good', label: 'Resale good' },
          ],
        },
        {
          name: 'tracking_mode', label: 'Tracking mode', type: 'select', options: [
            { value: 'stocked', label: 'Stocked — owns a balance' },
            { value: 'phantom', label: 'Phantom — recursively expands' },
          ],
        },
        { name: 'storage_zone', label: 'Storage zone / route' },
        { name: 'count_order', label: 'Count order', type: 'number' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
