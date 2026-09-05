'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  branchesApi,
  inventoryApi,
  type BranchInventorySettings,
  type ReportTemplate,
  type RecipeVersion,
  type ShiftInventoryReport,
  type StockAuditPreview,
  type VersionedRecipe,
} from '@/lib/pos-api';
import type {
  Branch,
  InventoryCategory,
  InventoryItem,
  InventoryLevel, InventoryTransaction,
  Supplier,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Select, Spinner, TabBar } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';
import { formatCurrency } from '@/lib/utils';

type TabKey = 'items' | 'levels' | 'recipes' | 'ledger' | 'counts' | 'shift-reports' | 'suppliers' | 'categories' | 'integrity';

export default function InventoryPage() {
  const [tab, setTab] = useState<TabKey>('items');

  return (
    <div>
      <div className="border-b border-gray-200 px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">Inventory</h1>
        <TabBar
          tabs={[
            { key: 'items', label: 'Items' },
            { key: 'levels', label: 'On hand' },
            { key: 'recipes', label: 'Recipes' },
            { key: 'ledger', label: 'Ledger' },
            { key: 'counts', label: 'Counts' },
            { key: 'shift-reports', label: 'Shift reports' },
            { key: 'suppliers', label: 'Suppliers' },
            { key: 'categories', label: 'Categories' },
            { key: 'integrity', label: 'Integrity' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as TabKey)}
        />
      </div>
      {tab === 'items' && <ItemsTab />}
      {tab === 'levels' && <LevelsTab />}
      {tab === 'recipes' && <RecipesTab />}
      {tab === 'ledger' && <LedgerTab />}
      {tab === 'counts' && <CountsTab />}
      {tab === 'shift-reports' && <ShiftReportsTab />}
      {tab === 'suppliers' && <SuppliersTab />}
      {tab === 'categories' && <CategoriesTab />}
      {tab === 'integrity' && <IntegrityTab />}
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
        kind: 'raw_material',
        tracking_mode: 'stocked',
        count_order: 0,
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
        { header: 'Kind', render: (i) => <Badge>{i.kind.replaceAll('_', ' ')}</Badge> },
        { header: 'Tracking', render: (i) => <Badge variant={i.tracking_mode === 'phantom' ? 'warning' : 'neutral'}>{i.tracking_mode}</Badge> },
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

function RecipesTab() {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [ownerKind, setOwnerKind] = useState('product');
  const [ownerId, setOwnerId] = useState('');
  const [recipe, setRecipe] = useState<VersionedRecipe | null>(null);
  const [draft, setDraft] = useState<RecipeVersion | null>(null);
  const [ingredientId, setIngredientId] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [message, setMessage] = useState('');

  useEffect(() => { void inventoryApi.items().then(setItems); }, []);
  const load = useCallback(async () => {
    if (!ownerId) return;
    try {
      const value = await inventoryApi.versionedRecipe(ownerKind, ownerId);
      setRecipe(value);
      setDraft(value.versions.find((v) => v.status === 'draft') ?? value.versions.find((v) => v.status === 'active') ?? null);
      setMessage('');
    } catch (err) {
      setRecipe(null);
      setDraft(null);
      setMessage(err instanceof ApiError && err.status === 404 ? 'No recipe yet. Add its first ingredient.' : 'Could not load recipe.');
    }
  }, [ownerId, ownerKind]);

  const save = async () => {
    if (!ownerId || !ingredientId || Number(quantity) <= 0) return;
    const existing = draft?.lines ?? [];
    const lines = [
      ...existing.filter((line) => line.item_id !== ingredientId).map((line) => ({
        item_id: line.item_id,
        quantity: line.quantity,
        yield_percentage: line.yield_percentage,
        inactive_in_order_types: line.inactive_in_order_types,
        display_order: line.display_order,
        source_metadata: line.source_metadata,
      })),
      { item_id: ingredientId, quantity, yield_percentage: '1', inactive_in_order_types: [], display_order: existing.length, source_metadata: {} },
    ];
    const saved = await inventoryApi.saveRecipeDraft(ownerKind, ownerId, { ingredients: lines, source: 'mm', source_metadata: {} });
    setDraft(saved);
    setMessage(`Draft v${saved.version_number} saved. It will not affect sales until activated.`);
    setIngredientId('');
  };

  return (
    <div className="p-6 max-w-5xl space-y-5">
      <div>
        <h2 className="font-display text-lg text-primary">Versioned recipes</h2>
        <p className="text-sm text-gray-500">Recipes contain only inventory items. Phantom items expand recursively; stocked items stop expansion.</p>
      </div>
      <div className="grid gap-3 md:grid-cols-[190px_1fr_auto] items-end">
        <Select label="Owner type" value={ownerKind} onChange={(e) => setOwnerKind(e.target.value)} options={[
          { value: 'product', label: 'Product' }, { value: 'modifier_option', label: 'Modifier option' }, { value: 'inventory_item', label: 'Inventory item' },
        ]} />
        {ownerKind === 'inventory_item' ? (
          <Select label="Recipe owner" value={ownerId} onChange={(e) => setOwnerId(e.target.value)} placeholder="Choose item" options={items.map((i) => ({ value: i.id, label: `${i.name} · ${i.sku}` }))} />
        ) : <Input label="Owner ID" value={ownerId} onChange={(e) => setOwnerId(e.target.value)} placeholder="Product or modifier-option UUID" />}
        <Button variant="outline" onClick={() => void load()} disabled={!ownerId}>Load</Button>
      </div>
      {message && <p className="rounded-sm bg-amber-50 px-3 py-2 text-sm text-amber-800">{message}</p>}
      <div className="border border-gray-200 p-4 space-y-3">
        <h3 className="font-medium">Draft ingredients</h3>
        {(draft?.lines ?? []).map((line) => {
          const item = items.find((value) => value.id === line.item_id);
          return <div key={line.item_id} className="flex justify-between border-b border-gray-100 py-2 text-sm"><span>{item?.name ?? line.item_id}</span><span>{line.quantity} {line.ingredient_unit}</span></div>;
        })}
        <div className="grid gap-3 md:grid-cols-[1fr_160px_auto] items-end">
          <Select label="Inventory item" value={ingredientId} onChange={(e) => setIngredientId(e.target.value)} placeholder="Add ingredient" options={items.map((i) => ({ value: i.id, label: `${i.name} · ${i.ingredient_unit}` }))} />
          <Input label="Quantity" type="number" min="0.0001" step="0.0001" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          <Button onClick={() => void save()} disabled={!ownerId || !ingredientId}>Save draft</Button>
        </div>
      </div>
      <div className="border border-gray-200 p-4">
        <div className="flex items-center justify-between"><h3 className="font-medium">Version history</h3>{draft?.status === 'draft' && <Button onClick={async () => { await inventoryApi.activateRecipe(draft.id); await load(); }} variant="outline">Activate v{draft.version_number}</Button>}</div>
        <div className="mt-3 space-y-2">{(recipe?.versions ?? []).map((version) => <div key={version.id} className="flex items-center gap-3 text-sm"><Badge variant={version.status === 'active' ? 'success' : version.status === 'draft' ? 'warning' : 'neutral'}>{version.status}</Badge><span>Version {version.version_number}</span><span className="text-gray-400">{version.lines.length} lines · {version.source}</span></div>)}</div>
      </div>
    </div>
  );
}

function BranchFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [branches, setBranches] = useState<Branch[]>([]);
  useEffect(() => { void branchesApi.list().then(setBranches); }, []);
  return <Select label="Branch" value={value} onChange={(e) => onChange(e.target.value)} placeholder="Choose branch" className="w-64" options={branches.map((b) => ({ value: b.id, label: b.name }))} />;
}

function LedgerTab({ countOnly = false }: { countOnly?: boolean }) {
  const [branchId, setBranchId] = useState('');
  const [rows, setRows] = useState<InventoryTransaction[]>([]);
  useEffect(() => { if (branchId) void inventoryApi.transactions({ branch_id: branchId, type: countOnly ? 'inventory_count' : undefined }).then(setRows); else setRows([]); }, [branchId, countOnly]);
  return <div className="p-6 max-w-[1500px] space-y-4"><BranchFilter value={branchId} onChange={setBranchId} /><p className="text-sm text-gray-500">{countOnly ? 'Physical counts post only the variance; levels are never edited directly.' : 'Every signed stock movement in immutable posting order, with source and running balance.'}</p><DataTable rows={rows} rowKey={(row) => row.id} columns={[
    { header: 'Seq', render: (row) => row.posting_sequence ?? 'Draft' },
    { header: 'Reference', priority: 'primary', render: (row) => row.reference },
    { header: 'Type', render: (row) => row.type.replaceAll('_', ' ') },
    { header: 'Source', render: (row) => row.source_type ? `${row.source_type} · ${row.source_id ?? ''}` : 'Manual' },
    { header: 'Movements', render: (row) => <div className="space-y-1">{row.items.map((line) => <div key={line.id} className="text-xs"><span className={Number(line.signed_quantity) < 0 ? 'text-red-600' : 'text-green-700'}>{Number(line.signed_quantity) > 0 ? '+' : ''}{line.signed_quantity ?? line.quantity}</span> {line.item_name} <span className="text-gray-400">→ {line.balance_after_quantity ?? '—'}</span></div>)}</div> },
    { header: 'Posted', render: (row) => row.posted_at ? new Date(row.posted_at).toLocaleString() : '—' },
  ]} /></div>;
}

function CountsTab() {
  const [branchId, setBranchId] = useState('');
  const [preview, setPreview] = useState<StockAuditPreview | null>(null);
  const [levels, setLevels] = useState<InventoryLevel[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    setPreview(null);
    setMessage('');
    if (!branchId) {
      setLevels([]);
      return;
    }
    void inventoryApi.levels({ branch_id: branchId }).then(setLevels).catch(() => setLevels([]));
  }, [branchId]);

  const downloadTemplate = () => {
    const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const rows = [
      ['SKU', 'Item name', 'Unit', 'Expected quantity', 'Counted quantity', 'Remark'],
      ...levels.map((level) => [
        level.item_sku,
        level.item_name,
        'ingredient',
        level.quantity,
        '',
        '',
      ]),
    ];
    const body = rows.map((row) => row.map(escape).join(',')).join('\n');
    const href = URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = `inventory-count-${branchId}.csv`;
    anchor.click();
    URL.revokeObjectURL(href);
  };

  const upload = async (file: File) => {
    if (!branchId) return;
    setBusy(true);
    try {
      setPreview(await inventoryApi.previewStockAuditFile(branchId, file));
      setMessage('Review every delta below. Nothing has posted yet.');
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not read count sheet.');
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview?.valid || !branchId) return;
    setBusy(true);
    try {
      const result = await inventoryApi.applyStockAudit({
        branch_id: branchId,
        idempotency_key: `admin-stock-audit:${crypto.randomUUID()}`,
        rows: preview.rows.map((row) => ({
          sku: row.sku,
          counted_quantity: row.counted_quantity,
          unit: row.unit as 'storage' | 'ingredient',
          remark: row.remark,
        })),
      });
      setPreview(result);
      setMessage(`Stock audit posted${result.transaction_id ? ` as ${result.transaction_id}` : ''}.`);
      setLevels(await inventoryApi.levels({ branch_id: branchId }));
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not post stock audit.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-6 max-w-[1400px] space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <BranchFilter value={branchId} onChange={setBranchId} />
        <Button variant="outline" onClick={downloadTemplate} disabled={!branchId || levels.length === 0}>
          Download CSV template
        </Button>
        <label className="inline-flex min-h-10 cursor-pointer items-center border border-primary px-4 text-sm text-primary hover:bg-primary/5 aria-disabled:cursor-not-allowed">
          {busy ? 'Reading…' : 'Preview CSV / XLSX'}
          <input
            className="sr-only"
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            disabled={!branchId || busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
              event.target.value = '';
            }}
          />
        </label>
      </div>
      <p className="text-sm text-gray-500">
        Upload a fresh physical count. Preview validates duplicate or unknown SKUs, units and precision; applying posts only physical minus current ledger quantity.
      </p>
      {message && <div className="border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">{message}</div>}
      {preview && (
        <>
          <DataTable rows={preview.rows} rowKey={(row) => `${row.sku}-${row.counted_quantity}-${row.errors.join('|')}`} columns={[
            { header: 'SKU', priority: 'secondary', render: (row) => <code className="text-xs">{row.sku}</code> },
            { header: 'Item', priority: 'primary', render: (row) => row.item_name ?? 'Unknown item' },
            { header: 'Expected', className: 'text-right', render: (row) => row.expected_quantity ?? '—' },
            { header: 'Counted', className: 'text-right', render: (row) => row.counted_quantity },
            { header: 'Delta', className: 'text-right', render: (row) => <span className={Number(row.delta_quantity) === 0 ? 'text-gray-500' : Number(row.delta_quantity) < 0 ? 'text-red-700' : 'text-green-700'}>{row.delta_quantity ?? '—'} {row.unit}</span> },
            { header: 'Validation', render: (row) => row.errors.length ? <span className="text-red-700">{row.errors.join('; ')}</span> : <Badge variant="success">Ready</Badge> },
            { header: 'Remark', render: (row) => row.remark ?? '—' },
          ]} />
          <div className="flex justify-end">
            <Button onClick={() => void apply()} loading={busy} disabled={!preview.valid || Boolean(preview.transaction_id)}>
              {preview.transaction_id ? 'Audit posted' : 'Post count deltas'}
            </Button>
          </div>
        </>
      )}
      <div className="border-t border-gray-200 pt-5">
        <h3 className="mb-3 font-medium text-gray-800">Posted count history</h3>
        <LedgerTab countOnly />
      </div>
    </div>
  );
}

function ShiftReportsTab() {
  const [branchId, setBranchId] = useState('');
  const [rows, setRows] = useState<ShiftInventoryReport[]>([]);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [name, setName] = useState('Closing stock reconciliation');
  const [reportType, setReportType] = useState('finished_goods');
  const [cadence, setCadence] = useState('per_business_day');
  const [required, setRequired] = useState(false);
  const [selectedItems, setSelectedItems] = useState<string[]>([]);
  const [message, setMessage] = useState('');

  const reload = useCallback(async () => {
    const [reports, reportTemplates] = await Promise.all([
      inventoryApi.shiftReports({ branch_id: branchId || undefined }),
      inventoryApi.reportTemplates(branchId || undefined),
    ]);
    setRows(reports);
    setTemplates(reportTemplates);
  }, [branchId]);

  useEffect(() => {
    void reload();
    void inventoryApi.items().then(setItems);
  }, [reload]);

  const createTemplate = async () => {
    if (!branchId || selectedItems.length === 0 || !name.trim()) return;
    await inventoryApi.createReportTemplate({
      branch_id: branchId,
      name: name.trim(),
      report_type: reportType as 'production' | 'finished_goods' | 'raw_materials' | 'packaging' | 'spot_check',
      cadence: cadence as 'per_till' | 'per_business_day' | 'ad_hoc',
      is_required: required,
      is_active: true,
      configuration: { visible_columns: ['opening', 'movements', 'expected', 'physical', 'variance', 'remark'] },
      approval_cost_threshold: '100',
      approval_variance_percent: '10',
      items: selectedItems.map((itemId, index) => ({
        item_id: itemId,
        display_order: index,
        required_input: reportType === 'production' ? 'production' : 'physical_count',
      })),
    });
    setMessage('Template created. It will be resolved into the next matching till/business-day checklist.');
    setSelectedItems([]);
    await reload();
  };

  return <div className="p-6 max-w-[1400px] space-y-5">
    <BranchFilter value={branchId} onChange={setBranchId} />
    <p className="text-sm text-gray-500">Outstanding reports remain visible after the till closes. Variances above branch tolerance wait for manager approval as one atomic movement.</p>
    <div className="border border-gray-200 p-4 space-y-3">
      <div className="flex items-center justify-between"><h3 className="font-medium text-gray-800">Branch report templates</h3><Badge>{templates.length} active/versioned</Badge></div>
      <div className="grid gap-3 md:grid-cols-4">
        <Input label="Template name" value={name} onChange={(event) => setName(event.target.value)} />
        <Select label="Type" value={reportType} onChange={(event) => setReportType(event.target.value)} options={[
          { value: 'production', label: 'Production' }, { value: 'finished_goods', label: 'Finished goods' }, { value: 'raw_materials', label: 'Raw materials' }, { value: 'packaging', label: 'Packaging' }, { value: 'spot_check', label: 'Spot check' },
        ]} />
        <Select label="Cadence" value={cadence} onChange={(event) => setCadence(event.target.value)} options={[
          { value: 'per_till', label: 'Per till' }, { value: 'per_business_day', label: 'Per business day' }, { value: 'ad_hoc', label: 'Ad hoc' },
        ]} />
        <label className="flex items-center gap-2 pt-7 text-sm"><input type="checkbox" checked={required} onChange={(event) => setRequired(event.target.checked)} />Required (may be deferred/waived)</label>
      </div>
      <label className="block text-xs uppercase tracking-wider text-gray-500">Items in physical count order</label>
      <select multiple value={selectedItems} onChange={(event) => setSelectedItems(Array.from(event.target.selectedOptions, option => option.value))} className="min-h-44 w-full border border-gray-300 bg-white p-2 text-sm">
        {items.slice().sort((a, b) => a.count_order - b.count_order || a.name.localeCompare(b.name)).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.kind.replaceAll('_', ' ')}</option>)}
      </select>
      <div className="flex items-center justify-between"><span className="text-xs text-gray-500">Select multiple items with Shift/Cmd. Default approval is AED 100 or 10%.</span><Button onClick={() => void createTemplate()} disabled={!branchId || selectedItems.length === 0}>Create template</Button></div>
      {message && <p className="bg-green-50 p-2 text-sm text-green-800">{message}</p>}
      {templates.length > 0 && <DataTable rows={templates} rowKey={(row) => row.id} columns={[
        { header: 'Template', priority: 'primary', render: (row) => row.name },
        { header: 'Type', render: (row) => row.report_type.replaceAll('_', ' ') },
        { header: 'Cadence', render: (row) => row.cadence.replaceAll('_', ' ') },
        { header: 'Version', render: (row) => `v${row.version_number}` },
        { header: 'Items', render: (row) => row.items.length },
        { header: 'Required', render: (row) => row.is_required ? <Badge variant="warning">Required</Badge> : 'Optional' },
      ]} />}
    </div>
    <h3 className="font-medium text-gray-800">Report submissions</h3>
    <DataTable rows={rows} rowKey={(row) => row.id} columns={[
      { header: 'Business date', render: (row) => row.business_date },
      { header: 'Report', priority: 'primary', render: (row) => String(row.template_snapshot.name ?? row.template_id) },
      { header: 'Status', render: (row) => <Badge variant={row.status === 'posted' ? 'success' : row.status === 'pending_approval' ? 'warning' : 'neutral'}>{row.status.replaceAll('_', ' ')}</Badge> },
      { header: 'Progress', render: (row) => `${row.lines.filter((line) => line.confirmed).length}/${row.lines.length}` },
      { header: 'Variance value', render: (row) => formatCurrency(row.lines.reduce((sum, line) => sum + Number(line.variance_cost ?? 0), 0)) },
    ]} />
  </div>;
}

function IntegrityTab() {
  const [branchId, setBranchId] = useState('');
  const [rows, setRows] = useState<Array<{ item_id: string; cached_quantity: string; ledger_quantity: string; cached_average_cost: string; ledger_average_cost: string }>>([]);
  const [settings, setSettings] = useState<BranchInventorySettings | null>(null);
  const [loading, setLoading] = useState(false);
  const check = async (apply = false) => { if (!branchId) return; setLoading(true); try { setRows(await (apply ? inventoryApi.rebuildProjection(branchId) : inventoryApi.projectionDrift(branchId))); } finally { setLoading(false); } };
  useEffect(() => { if (branchId) void inventoryApi.branchSettings(branchId).then(setSettings); else setSettings(null); }, [branchId]);
  const saveSettings = async () => { if (!settings) return; setLoading(true); try { setSettings(await inventoryApi.updateBranchSettings(settings.branch_id, settings)); } finally { setLoading(false); } };
  return <div className="p-6 max-w-5xl space-y-4"><BranchFilter value={branchId} onChange={setBranchId} />{settings && <div className="border border-gray-200 p-4 space-y-3"><div><h3 className="font-medium text-gray-800">Branch inventory rollout</h3><p className="text-xs text-gray-500">Inventory and sales consumption cannot be enabled until a manager-approved opening count records the go-live watermark.</p></div><div className="flex flex-wrap gap-5 text-sm"><label className="flex gap-2"><input type="checkbox" checked={settings.inventory_enabled} disabled={!settings.go_live_at} onChange={(event) => setSettings({ ...settings, inventory_enabled: event.target.checked })} />Inventory enabled</label><label className="flex gap-2"><input type="checkbox" checked={settings.sales_consumption_enabled} disabled={!settings.go_live_at} onChange={(event) => setSettings({ ...settings, sales_consumption_enabled: event.target.checked })} />Sales consumption</label><label className="flex gap-2"><input type="checkbox" checked={settings.production_enabled} onChange={(event) => setSettings({ ...settings, production_enabled: event.target.checked })} />Production</label><label className="flex gap-2"><input type="checkbox" checked={settings.validation_mode} onChange={(event) => setSettings({ ...settings, validation_mode: event.target.checked })} />Validation mode</label><label className="flex gap-2"><input type="checkbox" checked={settings.allow_negative_stock} onChange={(event) => setSettings({ ...settings, allow_negative_stock: event.target.checked })} />Allow negative</label></div><div className="flex items-center justify-between text-xs text-gray-500"><span>{settings.go_live_at ? `Opening count posted ${new Date(settings.go_live_at).toLocaleString()} · sequence ${settings.go_live_sequence}` : 'Awaiting opening count'}</span><Button size="sm" onClick={() => void saveSettings()} loading={loading}>Save settings</Button></div></div>}<div className="flex gap-2"><Button onClick={() => void check()} loading={loading} disabled={!branchId}>Preview ledger drift</Button><Button variant="outline" onClick={() => void check(true)} disabled={!branchId || loading}>Rebuild cache</Button></div><p className="text-sm text-gray-500">Rebuild replays closed ledger rows in posting-sequence order under the branch inventory lock.</p>{rows.length === 0 ? <div className="border border-green-200 bg-green-50 p-4 text-sm text-green-800">No projection drift found.</div> : <DataTable rows={rows} rowKey={(row) => row.item_id} columns={[
    { header: 'Item', render: (row) => row.item_id }, { header: 'Cached qty', render: (row) => row.cached_quantity }, { header: 'Ledger qty', render: (row) => row.ledger_quantity }, { header: 'Cached cost', render: (row) => row.cached_average_cost }, { header: 'Ledger cost', render: (row) => row.ledger_average_cost },
  ]} />}</div>;
}
