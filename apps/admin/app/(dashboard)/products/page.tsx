'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { productsApi, categoriesApi } from '@/lib/api';
import type { Category, Product } from '@/lib/types';
import { Badge, Button, Input, Select } from '@/components/ui';
import { formatCurrency } from '@/lib/utils';

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await productsApi.list({ search: search || undefined, category: categoryFilter || undefined, page, per_page: 20 });
      setProducts(res.items);
      setTotal(res.total);
      setPages(res.pages);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [search, categoryFilter, page]);

  useEffect(() => {
    categoriesApi.list(true).then(setCategories).catch(() => {});
  }, []);

  useEffect(() => {
    setPage(1);
  }, [search, categoryFilter]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDelete(slug: string, name: string) {
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
    setDeletingSlug(slug);
    try {
      await productsApi.delete(slug);
      setProducts(prev => prev.filter(p => p.slug !== slug));
      setTotal(t => t - 1);
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setDeletingSlug(null);
    }
  }

  const categoryOptions = [
    { value: '', label: 'All Categories' },
    ...categories.map(c => ({ value: c.slug, label: c.name })),
  ];

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Products</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{total} total</p>
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
        <div className="w-44">
          <Select
            value={categoryFilter}
            onChange={e => setCategoryFilter(e.target.value)}
            options={categoryOptions}
          />
        </div>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 w-12"></th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Product</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Category</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Price</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">SKU</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Modifiers</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Status</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 8 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : products.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No products found.
                </td>
              </tr>
            ) : (
              products.map(product => {
                return (
                  <tr key={product.id} className="hover:bg-gray-50 transition-colors">
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
                      <Badge variant={product.is_active ? 'success' : 'neutral'}>
                        {product.is_active ? 'Active' : 'Inactive'}
                      </Badge>
                      {product.is_featured && (
                        <Badge variant="info" className="ml-1">Featured</Badge>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Link href={`/products/${product.slug}/edit`}>
                          <Button variant="ghost" size="sm">Edit</Button>
                        </Link>
                        <Button
                          variant="danger"
                          size="sm"
                          loading={deletingSlug === product.slug}
                          onClick={() => handleDelete(product.slug, product.name)}
                        >
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <p className="text-xs text-gray-400 font-body">
            Page {page} of {pages} · {total} products
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>
              <span className="material-icons text-[14px]">chevron_left</span>
            </Button>
            <Button variant="ghost" size="sm" disabled={page === pages} onClick={() => setPage(p => p + 1)}>
              <span className="material-icons text-[14px]">chevron_right</span>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
