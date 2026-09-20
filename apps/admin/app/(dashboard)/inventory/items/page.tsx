'use client';

// Items — now with on-hand stock folded in (F4). The separate "On hand" tab is
// gone; instead each item carries one column per active branch showing its
// on-hand quantity there, its single-value columns (including the per-branch
// stock columns) are sortable from their headers, and a Stock filter surfaces
// low / below-par items across the whole estate. Recipes now live in their own
// section; a made item's row links out to it rather than editing inline.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  branchesApi,
  inventoryApi,
} from '@/lib/pos-api';
import type { Branch, InventoryCategory, InventoryItem, InventoryLevel, ItemCostLayers, Supplier } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Spinner } from '@/components/ui';
import { Modal, ResourcePage, StatusBadge, type ColumnDef } from '@/components/pos/ResourcePage';
import { RowAction } from '@/components/ui/DataTable';
import { formatCurrency, formatQuantity, interactiveRowClass } from '@/lib/utils';

// Made items (produced or semi-finished) are the only kinds that can own a recipe.
const MADE_KINDS = new Set(['produced_good', 'semi_finished']);

type BranchStock = { quantity: number; is_below_minimum: boolean };
type StockPivot = Map<string, Map<string, BranchStock>>;

export default function ItemsPage() {
  const [categories, setCategories] = useState<InventoryCategory[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [pivot, setPivot] = useState<StockPivot>(new Map());
  const [costItem, setCostItem] = useState<InventoryItem | null>(null);
  const router = useRouter();
  const [categoryId, setCategoryId] = useState('');
  const [kind, setKind] = useState('');
  const [trackingMode, setTrackingMode] = useState('');
  const [status, setStatus] = useState<'active' | 'inactive' | 'all'>('active');
  const [stockFilter, setStockFilter] = useState<'' | 'low' | 'below_par'>('');
  // '' = all, 'unmapped' = items with no supplier, otherwise a supplier id.
  const [supplierFilter, setSupplierFilter] = useState('');
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);

  useEffect(() => {
    void inventoryApi.categories().then(setCategories).catch(() => setCategories([]));
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
    // Suppliers here only populate the filter dropdown; each item already
    // carries its own `suppliers` from the items endpoint.
    void inventoryApi.suppliers({ include_inactive: true }).then(setSuppliers).catch(() => setSuppliers([]));
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
    if (supplierFilter) {
      const mapped = item.suppliers ?? [];
      if (supplierFilter === 'unmapped') {
        if (mapped.length) return false;
      } else if (!mapped.some((s) => s.supplier_id === supplierFilter)) {
        return false;
      }
    }
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
  }, [categoryId, kind, status, trackingMode, stockFilter, supplierFilter, pivot]);

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
      <select aria-label="Filter inventory supplier" value={supplierFilter} onChange={(event) => setSupplierFilter(event.target.value)} className="h-9 rounded border border-gray-300 bg-white px-2 text-xs text-gray-700">
        <option value="">All suppliers</option>
        <option value="unmapped">No supplier</option>
        {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
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
    <>
    <ResourcePage<InventoryItem>
      title="Inventory Items"
      description="Raw materials and tracked goods. Each item is bought in a storage unit and consumed in an ingredient unit; stock columns show the on-hand quantity at each branch."
      paginated
      stickyHeader
      load={load}
      create={(d) => inventoryApi.createItem(d)}
      update={(id, d) => inventoryApi.updateItem(id, d)}
      remove={(id) => inventoryApi.removeItem(id)}
      searchKeys={['name', 'sku']}
      toolbar={toolbar}
      filterRows={filterRows}
      rowActions={(item) => (
        <>
          {MADE_KINDS.has(item.kind) && (
            <RowAction onClick={() => router.push(`/recipes/inventory?open=${item.id}`)}>Recipe</RowAction>
          )}
          {item.tracking_mode !== 'phantom' && (
            <RowAction onClick={() => setCostItem(item)}>Cost</RowAction>
          )}
        </>
      )}
      defaults={{
        storage_unit: 'kg',
        ingredient_unit: 'g',
        storage_to_ingredient_factor: 1000,
        minimum_level: 0,
        maximum_level: 0,
        par_level: 0,
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
          header: 'Suppliers',
          priority: 'secondary',
          className: 'max-w-xs align-top',
          render: (i) =>
            i.suppliers && i.suppliers.length ? (
              <span className="block whitespace-normal break-words text-xs text-gray-600">
                {i.suppliers.map((s) => s.supplier_name).join(', ')}
              </span>
            ) : (
              <span className="text-gray-400">—</span>
            ),
        },
        {
          header: 'Units',
          render: (i) => (
            <span className="text-xs text-gray-600">
              1 {i.storage_unit} = {Number(i.storage_to_ingredient_factor)} {i.ingredient_unit}
            </span>
          ),
        },
        { header: 'Cost', render: (i) => formatCurrency(i.average_cost) },
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
    {costItem && <CostLayersModal item={costItem} onClose={() => setCostItem(null)} />}
    </>
  );
}

function CostLayersModal({ item, onClose }: { item: InventoryItem; onClose: () => void }) {
  const [data, setData] = useState<ItemCostLayers | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    inventoryApi
      .itemCostLayers(item.id)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load cost layers.'); });
    return () => { cancelled = true; };
  }, [item.id]);

  return (
    <Modal title={`Cost layers — ${item.name}`} onClose={onClose} wide>
      <p className="mb-3 text-xs text-gray-500 font-body">
        Stock is valued first-in, first-out: each layer is a quantity still on the shelf at the
        cost it arrived at, oldest first (the order the next issue consumes them). The average is
        what these layers imply.
      </p>
      {error && <p className="text-xs text-red-600 font-body">{error}</p>}
      {!data ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : data.layers.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400 font-body">
          No costed stock. A purchase will lay down the first layer.
        </p>
      ) : (
        <>
          <div className="mb-3 flex gap-6 text-sm">
            <div><span className="text-gray-500 font-body">On hand</span><br /><span className="font-display text-primary">{formatQuantity(data.total_quantity)} {item.storage_unit}</span></div>
            <div><span className="text-gray-500 font-body">Value</span><br /><span className="font-display text-primary">{formatCurrency(data.total_value)}</span></div>
            <div><span className="text-gray-500 font-body">Avg cost</span><br /><span className="font-display text-primary">{formatCurrency(data.average_cost)}</span></div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                <th className="py-2 text-left">Source</th>
                <th className="py-2 text-left">Warehouse</th>
                <th className="py-2 text-right">Remaining</th>
                <th className="py-2 text-right">Unit cost</th>
                <th className="py-2 text-right">Value</th>
                <th className="py-2 text-right">Received</th>
              </tr>
            </thead>
            <tbody>
              {data.layers.map((layer) => (
                <tr key={layer.id} className={`border-b border-gray-100 ${interactiveRowClass}`}>
                  <td className="py-2"><Badge>{layer.source_kind.replaceAll('_', ' ')}</Badge></td>
                  <td className="py-2 text-gray-600">{layer.warehouse_name ?? '—'}</td>
                  <td className="py-2 text-right">{formatQuantity(layer.remaining_quantity)}</td>
                  <td className="py-2 text-right">{formatCurrency(layer.unit_cost)}</td>
                  <td className="py-2 text-right">{formatCurrency(layer.remaining_quantity * layer.unit_cost)}</td>
                  <td className="py-2 text-right text-gray-500">{layer.received_at.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Modal>
  );
}
