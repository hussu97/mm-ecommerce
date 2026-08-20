'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { productsApi, categoriesApi, bulkApi } from '@/lib/api';
import type { Category, Product, SalesChannel } from '@/lib/types';
import { ChannelBadges } from '@/components/products/SalesChannels';
import { Badge, Button, Input, MultiSelect, Pagination, TabBar, LoadError, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { formatCurrency } from '@/lib/utils';
import { BranchStockBadges, useBranchStock } from '@/components/products/BranchStock';

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
  // This page used to fire a request per keystroke with nothing dropping the
  // stale responses; the debounce plus the hook's sequence guard fix both.
  const debouncedSearch = useDebouncedValue(search);

  // Server-side pagination: `/products` pages, searches and filters in SQL.
  const fetchProducts = useCallback(
    (page: number, perPage: number) => {
      const base = {
        search: debouncedSearch || undefined,
        category: categoryFilter.length > 0 ? categoryFilter : undefined,
        sort: 'category',
        page,
        per_page: perPage,
      };
      return productsApi.list(
        activeTab === 'active'
          ? { ...base, is_active: true }
          : { ...base, include_inactive: true, is_active: false },
      );
    },
    [debouncedSearch, categoryFilter, activeTab],
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
        <Link href="/products/new">
          <Button>
            <span className="material-icons text-[14px]">add</span>
            New Product
          </Button>
        </Link>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-4">
        <div className="flex-1 max-w-xs">
          <Input
            placeholder="Search products..."
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
          <button onClick={() => setSelectedIds(new Set(products.map(p => p.id)))} className="text-xs font-body text-gray-500 hover:text-primary underline">All</button>
          <button onClick={() => setSelectedIds(new Set())} className="text-xs font-body text-gray-500 hover:text-primary underline">None</button>
          <div className="flex-1" />
          <span className="text-xs font-body text-gray-500">Website</span>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkVisibility('web', true)}>Show</Button>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkVisibility('web', false)}>Hide</Button>
          <span className="text-xs font-body text-gray-500 ml-2">Register</span>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkVisibility('pos', true)}>Show</Button>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkVisibility('pos', false)}>Hide</Button>
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
              render: p =>
                p.base_price > 0 ? formatCurrency(p.base_price) : 'From options',
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
              render: p => (
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
                  {p.is_featured && <Badge variant="info">Featured</Badge>}
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
