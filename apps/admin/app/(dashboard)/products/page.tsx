'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { productsApi, categoriesApi, bulkApi, exportApi } from '@/lib/api';
import type { Category, Product, SalesChannel } from '@/lib/types';
import { PRODUCT_LABEL_LABELS } from '@/lib/types';
import { ChannelBadges } from '@/components/products/SalesChannels';
import { Badge, Button, Input, MultiSelect, Pagination, TabBar, LoadError, Spinner } from '@/components/ui';
import { DataTable, type SortState } from '@/components/ui/DataTable';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { formatCurrency } from '@/lib/utils';
import {
  BranchStockBadges,
  ModifierBranchStockBadges,
  productOptionIds,
  useBranchStock,
  useModifierStock,
} from '@/components/products/BranchStock';
import { CostCell, CostPctCell, PriceCell, useProductCosts } from '@/components/products/ProductCost';

export default function ProductsPage() {
  const toast = useToast();
  const confirm = useConfirm();
  const [categories, setCategories] = useState<Category[]>([]);
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active');
  const [tabCounts, setTabCounts] = useState<{ active?: number; inactive?: number }>({});
  const [actionSlug, setActionSlug] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulking, setBulking] = useState(false);
  const [exportingCosts, setExportingCosts] = useState(false);
  // This page used to fire a request per keystroke with nothing dropping the
  // stale responses; the debounce plus the hook's sequence guard fix both.
  const debouncedSearch = useDebouncedValue(search);
  // Null is the default grouping by category. Price, cost and cost % sort on
  // the server across every page — cost by the recipe, as the columns show it.
  const [sort, setSort] = useState<SortState | null>(null);

  // Server-side pagination: `/products` pages, searches and filters in SQL.
  const fetchProducts = useCallback(
    (page: number, perPage: number) => {
      const base = {
        search: debouncedSearch || undefined,
        category: categoryFilter.length > 0 ? categoryFilter : undefined,
        sort: sort ? `${sort.key}_${sort.direction}` : 'category',
        page,
        per_page: perPage,
      };
      return productsApi.list(
        activeTab === 'active'
          ? { ...base, is_active: true }
          : { ...base, include_inactive: true, is_active: false },
      );
    },
    [debouncedSearch, categoryFilter, activeTab, sort],
  );

  const {
    items: products, total, pages, page, perPage, setPage, setPerPage,
    loading, loadError, refetch,
  } = useApiList<Product>({ paginate: 'server', fetch: fetchProducts });

  // One fetch of the branches and one of every override, shared by the whole
  // page. These tables are exception-only, so the answer is a few dozen rows
  // however long the list gets — which is what makes a per-branch column
  // affordable rather than a request per row.
  const { branches, statusOf } = useBranchStock();
  // Recipe cost vs price for the rows on screen: one request per page.
  const costs = useProductCosts(products.map(p => p.id));
  // The option-level twin, for products that have modifiers: their branch-stock
  // cell reads "how many fillings are in stock here" rather than the rarely-used
  // product-level flag. One shared fetch, same as the product overrides above.
  const { statusOf: modifierStatusOf } = useModifierStock();

  // Load categories + pre-fetch inactive count on mount
  useEffect(() => {
    categoriesApi.list(true).then(setCategories).catch(() => {});
    productsApi.list({ is_active: false, include_inactive: true, per_page: 1 })
      .then(r => setTabCounts(prev => ({ ...prev, inactive: r.total })))
      .catch(() => {});
  }, []);

  // The tab badge shows the last total the open tab reported. Only written
  // once a load settles, so a half-finished tab switch cannot stamp the old
  // tab's count onto the new one.
  useEffect(() => {
    if (!loading && !loadError) setTabCounts(prev => ({ ...prev, [activeTab]: total }));
  }, [loading, loadError, total, activeTab]);

  // A different filter set means a different result set: drop the selection.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [fetchProducts]);

  async function handleDeactivate(slug: string, name: string) {
    if (!(await confirm({
      title: 'Deactivate product',
      message: `Deactivate "${name}"? It will move to the Inactive tab.`,
      confirmLabel: 'Deactivate',
      danger: true,
    }))) return;
    setActionSlug(slug);
    try {
      await productsApi.delete(slug);
      await refetch();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionSlug(null);
    }
  }

  async function handleRestore(slug: string) {
    setActionSlug(slug);
    try {
      await productsApi.update(slug, { is_active: true });
      await refetch();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionSlug(null);
    }
  }

  function toggleSelect(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selectedIds.size === products.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(products.map(p => p.id)));
    }
  }

  async function handleBulkStatus(is_active: boolean) {
    const n = selectedIds.size;
    // Deactivating in bulk is the destructive direction, and single deactivate
    // already asks — so a stroke that takes up to a page of products off sale
    // must ask too, and name the count (F-ADM-6). Activating is safe and does not.
    if (
      !is_active &&
      !(await confirm({
        title: 'Deactivate products',
        message: `Deactivate ${n} ${n === 1 ? 'product' : 'products'}? They will move to the Inactive tab.`,
        confirmLabel: `Deactivate ${n}`,
        danger: true,
      }))
    )
      return;
    setBulking(true);
    try {
      await bulkApi.updateStatus('products', Array.from(selectedIds), is_active);
      await refetch();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBulking(false);
    }
  }

  /**
   * Put the selection on or off one channel, leaving the other alone.
   *
   * Separate from activate/deactivate on purpose: taking lattes off the cake
   * website should not withdraw them from the counter too.
   */
  async function handleBulkVisibility(channel: SalesChannel, visible: boolean) {
    const n = selectedIds.size;
    // Hiding is the destructive direction — it withdraws products from a
    // storefront — so it confirms with a count; showing does not (F-ADM-6).
    if (
      !visible &&
      !(await confirm({
        title: 'Hide products',
        message: `Hide ${n} ${n === 1 ? 'product' : 'products'} from the website?`,
        confirmLabel: `Hide ${n}`,
        danger: true,
      }))
    )
      return;
    setBulking(true);
    try {
      await bulkApi.updateVisibility(Array.from(selectedIds), channel, visible);
      await refetch();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBulking(false);
    }
  }

  const handleExportCosts = async () => {
    setExportingCosts(true);
    try {
      await exportApi.productCosts();
    } catch (e) {
      toast.error((e as Error).message || 'Could not export product costs');
    } finally {
      setExportingCosts(false);
    }
  };

  const categoryOptions = categories.map(c => ({ value: c.slug, label: `${c.name} (${c.product_count})` }));

  return (
    <div>
      <LoadError message={loadError} onRetry={refetch} />
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Products</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{total} {activeTab}</p>
        </div>
        <div className="flex items-center gap-2">
          {/* The whole catalogue's cost vs price and the recipes behind it. */}
          <Button variant="outline" loading={exportingCosts} onClick={handleExportCosts}>
            <span className="material-icons text-[14px]">download</span>
            Export costs
          </Button>
          <Link href="/products/new">
            <Button>
              <span className="material-icons text-[14px]">add</span>
              New Product
            </Button>
          </Link>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-4">
        <div className="flex-1 max-w-xs">
          <Input
            placeholder="Search by name or SKU..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="w-52">
          <MultiSelect
            options={categoryOptions}
            value={categoryFilter}
            onChange={setCategoryFilter}
            placeholder="All Categories"
          />
        </div>
      </div>

      {/* Tabs */}
      <TabBar
        tabs={[
          { key: 'active', label: 'Active', count: tabCounts.active },
          { key: 'inactive', label: 'Inactive', count: tabCounts.inactive },
        ]}
        active={activeTab}
        onChange={key => setActiveTab(key as 'active' | 'inactive')}
      />

      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 bg-primary/10 border border-primary/30 px-4 py-2.5 mb-4">
          <span className="text-xs font-body text-primary font-medium">{selectedIds.size} selected</span>
          <button onClick={() => setSelectedIds(new Set(products.map(p => p.id)))} className="text-xs font-body text-gray-500 hover:text-primary underline">All on this page</button>
          <button onClick={() => setSelectedIds(new Set())} className="text-xs font-body text-gray-500 hover:text-primary underline">None</button>
          <div className="flex-1" />
          <span className="text-xs font-body text-gray-500">Website</span>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkVisibility('web', true)}>Show</Button>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkVisibility('web', false)}>Hide</Button>
          {/* Register placement is set in Menu Groups now, not by a per-product flag. */}
          <div className="w-px h-5 bg-primary/30 mx-1" />
          <Button size="sm" loading={bulking} onClick={() => handleBulkStatus(true)}>Activate</Button>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkStatus(false)}>Deactivate</Button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<Product>
          rows={products}
          rowKey={p => p.id}
          rowClassName={p => (selectedIds.has(p.id) ? 'bg-primary/5' : undefined)}
          sort={sort}
          onSortChange={next => {
            setSort(next);
            setPage(1);
          }}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">No products found.</p>
          }
          actions={product =>
            activeTab === 'active' ? (
              <>
                <Link href={`/products/${product.slug}/edit`} onClick={e => e.stopPropagation()}>
                  <Button variant="ghost" size="sm">Edit</Button>
                </Link>
                <Button
                  variant="danger"
                  size="sm"
                  loading={actionSlug === product.slug}
                  onClick={() => handleDeactivate(product.slug, product.name)}
                >
                  Deactivate
                </Button>
              </>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                loading={actionSlug === product.slug}
                onClick={() => handleRestore(product.slug)}
              >
                Restore
              </Button>
            )
          }
          columns={[
            {
              header: '',
              // The select-all checkbox lives in the header on desktop. A card
              // list has no header row, so bulk selection is a desktop
              // affordance here and the column says so.
              priority: 'desktop',
              className: 'w-8',
              headerRender: () => (
                <input
                  type="checkbox"
                  checked={products.length > 0 && selectedIds.size === products.length}
                  onChange={toggleSelectAll}
                  className="accent-primary"
                />
              ),
              render: p => (
                <input
                  type="checkbox"
                  checked={selectedIds.has(p.id)}
                  onChange={() => toggleSelect(p.id)}
                  onClick={e => e.stopPropagation()}
                  className="accent-primary"
                />
              ),
            },
            {
              header: 'Product',
              priority: 'primary',
              render: p => (
                <div className="flex items-center gap-3">
                  {p.image_urls[0] ? (
                    <div className="relative w-9 h-9 shrink-0">
                      <Image
                        src={p.image_urls[0]}
                        alt={p.name}
                        fill
                        sizes="36px"
                        className="object-cover rounded-sm"
                      />
                    </div>
                  ) : (
                    <div className="w-9 h-9 shrink-0 bg-gray-100 flex items-center justify-center rounded-sm">
                      <span className="material-icons text-gray-300 text-[16px]">image</span>
                    </div>
                  )}
                  <span className="min-w-0 font-body font-medium text-gray-800 text-sm break-words">
                    {p.name}
                  </span>
                </div>
              ),
            },
            { header: 'Slug', priority: 'secondary', render: p => p.slug },
            { header: 'Category', render: p => p.category?.name ?? '—' },
            {
              header: 'Price',
              className: 'text-right',
              sortable: true,
              sortKey: 'price',
              render: p => (
                <PriceCell
                  cost={costs.get(p.id)}
                  fallback={p.base_price > 0 ? formatCurrency(p.base_price) : 'From options'}
                />
              ),
            },
            {
              // With options, a row sorts by its cheapest priced option — the
              // one its "From" price shows. Unknown cost (no recipe) sorts last.
              header: 'Cost',
              className: 'text-right',
              sortable: true,
              sortKey: 'cost',
              render: p => <CostCell cost={costs.get(p.id)} />,
            },
            {
              header: 'Cost %',
              className: 'text-right',
              sortable: true,
              sortKey: 'cost_pct',
              render: p => <CostPctCell cost={costs.get(p.id)} />,
            },
            { header: 'SKU', render: p => p.sku ?? '—' },
            {
              header: 'Modifiers',
              className: 'text-center',
              render: p => p.product_modifiers.length,
            },
            {
              // Where the register's "86 it" button shows up in the console.
              // Until now this state was invisible here — and since the
              // storefront started answering per branch, an item marked out at
              // one kitchen is gone from that emirate's website with nothing on
              // this screen to say so.
              header: 'Branch stock',
              className: 'text-center',
              render: p =>
                p.product_modifiers.length > 0 ? (
                  <ModifierBranchStockBadges
                    optionIds={productOptionIds(p.product_modifiers)}
                    branches={branches}
                    statusOf={modifierStatusOf}
                  />
                ) : (
                  <BranchStockBadges
                    productId={p.id}
                    branches={branches}
                    statusOf={statusOf}
                  />
                ),
            },
            {
              header: 'Channels',
              className: 'text-center',
              render: p => (
                <>
                  {(p.labels ?? []).map(label => (
                    <Badge key={label} variant="info">
                      {PRODUCT_LABEL_LABELS[label] ?? label}
                    </Badge>
                  ))}
                  <ChannelBadges channels={p.sales_channels} />
                </>
              ),
            },
          ]}
        />
      )}

      <Pagination
        page={page}
        pages={pages}
        total={total}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={setPerPage}
        label="products"
      />
    </div>
  );
}
