'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { productsApi, categoriesApi, bulkApi } from '@/lib/api';
import type { Category, Product } from '@/lib/types';
import { Badge, Button, Input, MultiSelect, Pagination, TabBar } from '@/components/ui';
import { formatCurrency } from '@/lib/utils';

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active');
  const [perPage, setPerPage] = useState(50);
  const [tabCounts, setTabCounts] = useState<{ active?: number; inactive?: number }>({});
  const [actionSlug, setActionSlug] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulking, setBulking] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const base = {
        search: search || undefined,
        category: categoryFilter.length > 0 ? categoryFilter : undefined,
        page,
        per_page: perPage,
      };
      const params = activeTab === 'active'
        ? { ...base, is_active: true }
        : { ...base, include_inactive: true, is_active: false };
      const res = await productsApi.list(params);
      setProducts(res.items);
      setTotal(res.total);
      setPages(res.pages);
      setTabCounts(prev => ({ ...prev, [activeTab]: res.total }));
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [search, categoryFilter, page, perPage, activeTab]);

  // Load categories + pre-fetch inactive count on mount
  useEffect(() => {
    categoriesApi.list(true).then(setCategories).catch(() => {});
    productsApi.list({ is_active: false, include_inactive: true, per_page: 1 })
      .then(r => setTabCounts(prev => ({ ...prev, inactive: r.total })))
      .catch(() => {});
  }, []);

  useEffect(() => {
    setPage(1);
  }, [search, categoryFilter, activeTab]);

  useEffect(() => {
    load();
    setSelectedIds(new Set());
  }, [load]);

  async function handleDeactivate(slug: string, name: string) {
    if (!confirm(`Deactivate "${name}"? It will move to the Inactive tab.`)) return;
    setActionSlug(slug);
    try {
      await productsApi.delete(slug);
      setProducts(prev => prev.filter(p => p.slug !== slug));
      setTotal(t => t - 1);
      setTabCounts(prev => ({ ...prev, active: (prev.active ?? 1) - 1 }));
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setActionSlug(null);
    }
  }

  async function handleRestore(slug: string) {
    setActionSlug(slug);
    try {
      await productsApi.update(slug, { is_active: true });
      setProducts(prev => prev.filter(p => p.slug !== slug));
      setTotal(t => t - 1);
      setTabCounts(prev => ({ ...prev, inactive: (prev.inactive ?? 1) - 1 }));
    } catch (err) {
      alert((err as Error).message);
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
      await load();
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setBulking(false);
    }
  }

  const categoryOptions = categories.map(c => ({ value: c.slug, label: `${c.name} (${c.product_count})` }));

  return (
    <div>
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
          <Button size="sm" loading={bulking} onClick={() => handleBulkStatus(true)}>Activate</Button>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkStatus(false)}>Deactivate</Button>
        </div>
      )}

      {/* Table */}
      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 w-8">
                <input
                  type="checkbox"
                  checked={products.length > 0 && selectedIds.size === products.length}
                  onChange={toggleSelectAll}
                  className="accent-primary"
                />
              </th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 w-12"></th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Product</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Category</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Price</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">SKU</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Modifiers</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Featured</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 9 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : products.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No products found.
                </td>
              </tr>
            ) : (
              products.map(product => (
                <tr key={product.id} className={`hover:bg-gray-50 transition-colors ${selectedIds.has(product.id) ? 'bg-primary/5' : ''}`}>
                  <td className="px-4 py-2.5 w-8">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(product.id)}
                      onChange={() => toggleSelect(product.id)}
                      className="accent-primary"
                    />
                  </td>
                  <td className="px-4 py-2.5">
                    {product.image_urls[0] ? (
                      <div className="relative w-9 h-9 shrink-0">
                        <Image
                          src={product.image_urls[0]}
                          alt={product.name}
                          fill
                          sizes="36px"
                          className="object-cover rounded-sm"
                        />
                      </div>
                    ) : (
                      <div className="w-9 h-9 bg-gray-100 flex items-center justify-center rounded-sm">
                        <span className="material-icons text-gray-300 text-[16px]">image</span>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="font-body font-medium text-gray-800 text-sm">{product.name}</div>
                    <div className="font-body text-[11px] text-gray-400">{product.slug}</div>
                  </td>
                  <td className="px-4 py-2.5 hidden sm:table-cell">
                    <span className="text-xs font-body text-gray-500">{product.category?.name ?? '—'}</span>
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <span className="text-xs font-body text-gray-700">
                      {product.base_price > 0 ? formatCurrency(product.base_price) : 'From options'}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 hidden md:table-cell">
                    <span className="text-xs font-body text-gray-400">{product.sku ?? '—'}</span>
                  </td>
                  <td className="px-4 py-2.5 text-center hidden md:table-cell">
                    <span className="text-xs font-body text-gray-500">
                      {product.product_modifiers.length}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-center hidden lg:table-cell">
                    {product.is_featured && <Badge variant="info">Featured</Badge>}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="flex items-center justify-end gap-2">
                      {activeTab === 'active' ? (
                        <>
                          <Link href={`/products/${product.slug}/edit`}>
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
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

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
