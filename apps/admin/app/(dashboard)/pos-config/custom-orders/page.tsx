'use client';

/**
 * The three settings custom orders run on (business settings):
 *
 * - **Branch** — where every custom order is made, stocked from and sent from
 *   (Sharjah Kitchen in production). Its registers get the kitchen docket and
 *   the POS section.
 * - **Product** — the open-price product each line is sold as (FG0119 "Cake -
 *   Customer Specification"), which carries the tax group.
 * - **Inventory category** — where a recipe's items come from ("Customized Cake
 *   Raw Materials").
 *
 * Until all three are set the channel is off, and the Custom Orders screen says
 * which one is missing and links here.
 */

import { useEffect, useMemo, useState } from 'react';
import { ApiError, customOrdersApi, productsApi, type CustomOrdersStatus } from '@/lib/api';
import { branchesApi, businessSettingsApi, inventoryApi } from '@/lib/pos-api';
import type { Branch, BusinessSettings, InventoryCategory } from '@/lib/pos-types';
import type { Product } from '@/lib/types';
import { Badge, Button, Input, LoadError, Select, Spinner } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';

export default function CustomOrderSettingsPage() {
  const toast = useToast();
  const [settings, setSettings] = useState<BusinessSettings | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<InventoryCategory[]>([]);
  const [status, setStatus] = useState<CustomOrdersStatus | null>(null);
  const [loadError, setLoadError] = useState('');
  const [tick, setTick] = useState(0);

  const [branchId, setBranchId] = useState('');
  const [productId, setProductId] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [productSearch, setProductSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoadError('');
    Promise.all([
      businessSettingsApi.get(),
      branchesApi.list(),
      // The whole catalogue in one read — a few hundred products at most — so the
      // search below filters locally and the current choice is always resolvable.
      productsApi.list({ per_page: 2000, include_inactive: true }),
      inventoryApi.categories(),
    ])
      .then(([s, b, p, c]) => {
        setSettings(s);
        setBranches(b.filter(x => !x.deleted_at));
        setProducts(p.items);
        setCategories(c.filter(x => !x.deleted_at));
        setBranchId(s.custom_orders_branch_id ?? '');
        setProductId(s.custom_orders_product_id ?? '');
        setCategoryId(s.custom_orders_inventory_category_id ?? '');
      })
      .catch(err => setLoadError(err instanceof ApiError ? err.message : 'Could not load the settings.'));
    // The channel's own verdict; needs the custom-orders permission, so it may 403.
    customOrdersApi.status().then(setStatus).catch(() => setStatus(null));
  }, [tick]);

  const productOptions = useMemo(() => {
    const q = productSearch.trim().toLowerCase();
    const matches = products.filter(
      p => !q || p.name.toLowerCase().includes(q) || (p.sku ?? '').toLowerCase().includes(q),
    );
    // The current choice stays in the list whatever the search, so the select
    // never silently shows a different product than the one saved.
    const current = products.find(p => p.id === productId);
    const list = current && !matches.includes(current) ? [current, ...matches] : matches;
    return list.slice(0, 200).map(p => ({
      value: p.id,
      label: `${p.sku ? `${p.sku} · ` : ''}${p.name}${p.is_active ? '' : ' (inactive)'}`,
    }));
  }, [products, productSearch, productId]);

  const dirty =
    settings !== null &&
    (branchId !== (settings.custom_orders_branch_id ?? '') ||
      productId !== (settings.custom_orders_product_id ?? '') ||
      categoryId !== (settings.custom_orders_inventory_category_id ?? ''));

  async function save() {
    setSaving(true);
    setError('');
    try {
      const next = await businessSettingsApi.update({
        custom_orders_branch_id: branchId || null,
        custom_orders_product_id: productId || null,
        custom_orders_inventory_category_id: categoryId || null,
      });
      setSettings(next);
      toast.success('Custom-order settings saved.');
      customOrdersApi.status().then(setStatus).catch(() => setStatus(null));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <LoadError message={loadError} onRetry={() => setTick(t => t + 1)} />;
  if (!settings) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="max-w-[var(--content-max)]">
      <section className="bg-white border border-gray-200 p-4">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-display text-lg text-gray-800">Custom orders</h2>
            <p className="mt-0.5 text-xs font-body text-gray-500">
              Bespoke cakes taken in the console or at the register. All three must be set for the
              channel to be on.
            </p>
          </div>
          {status && (
            <Badge variant={status.enabled ? 'success' : 'warning'}>
              {status.enabled ? `On · ${status.branch_name}` : 'Off'}
            </Badge>
          )}
        </div>

        <div className="space-y-4">
          <div>
            <Select
              id="co-branch"
              label="Branch"
              value={branchId}
              placeholder="Not set"
              onChange={e => setBranchId(e.target.value)}
              options={branches.map(b => ({ value: b.id, label: `${b.reference} · ${b.name}` }))}
            />
            <p className="mt-1 text-xs text-gray-400">
              Where custom orders are made and sent from. Its registers print the kitchen docket.
            </p>
          </div>

          <div>
            <p className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">Product</p>
            <div className="grid gap-2 sm:grid-cols-[14rem_1fr]">
              <Input
                id="co-product-search"
                placeholder="Search name or SKU"
                value={productSearch}
                onChange={e => setProductSearch(e.target.value)}
              />
              <Select
                id="co-product"
                aria-label="Product"
                value={productId}
                placeholder="Not set"
                onChange={e => setProductId(e.target.value)}
                options={productOptions}
              />
            </div>
            <p className="mt-1 text-xs text-gray-400">
              The open-price product every custom-order line is sold as; its tax group prices the VAT.
            </p>
          </div>

          <div>
            <Select
              id="co-category"
              label="Recipe inventory category"
              value={categoryId}
              placeholder="Not set"
              onChange={e => setCategoryId(e.target.value)}
              options={categories.map(c => ({
                value: c.id,
                label: `${c.name}${c.is_active ? '' : ' (inactive)'}`,
              }))}
            />
            <p className="mt-1 text-xs text-gray-400">
              The items a custom order&rsquo;s recipe may use, consumed from the branch&rsquo;s stock at packing.
            </p>
          </div>
        </div>

        {error && <p className="mt-3 text-sm text-red-600 font-body">{error}</p>}
        <div className="mt-4 flex justify-end">
          <Button onClick={save} loading={saving} disabled={!dirty || saving}>
            Save
          </Button>
        </div>
      </section>
    </div>
  );
}
