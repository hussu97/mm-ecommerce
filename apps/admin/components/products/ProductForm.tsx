'use client';

import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { categoriesApi, productsApi, uploadsApi, ApiError } from '@/lib/api';
import type { Category, Product } from '@/lib/types';
import { Button, Input, Select, Textarea } from '@/components/ui';
import { slugify } from '@/lib/utils';

interface VariantRow {
  id?: string;
  name: string;
  sku: string;
  price: string;
  stock_quantity: string;
  display_order: number;
  is_active: boolean;
  _deleted?: boolean;
}

interface Props {
  product?: Product;
}

export function ProductForm({ product }: Props) {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [categories, setCategories] = useState<Category[]>([]);
  const [form, setForm] = useState({
    name: product?.name ?? '',
    slug: product?.slug ?? '',
    category_id: product?.category_id ?? '',
    description: product?.description ?? '',
    base_price: String(product?.base_price ?? ''),
    is_featured: product?.is_featured ?? false,
    is_active: product?.is_active ?? true,
    display_order: String(product?.display_order ?? 0),
  });
  const [imageUrls, setImageUrls] = useState<string[]>(product?.image_urls ?? []);
  const [variants, setVariants] = useState<VariantRow[]>(
    product?.variants.map(v => ({
      id: v.id,
      name: v.name,
      sku: v.sku,
      price: String(v.price),
      stock_quantity: String(v.stock_quantity),
      display_order: v.display_order,
      is_active: v.is_active,
    })) ?? []
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [uploadingIdx, setUploadingIdx] = useState<number | null>(null);
  const [apiError, setApiError] = useState('');

  // Auto-slug only on create
  const isEdit = !!product;
  useEffect(() => {
    if (!isEdit) setForm(f => ({ ...f, slug: slugify(f.name) }));
  }, [form.name, isEdit]);

  useEffect(() => {
    categoriesApi.list(true).then(setCategories).catch(() => {});
  }, []);

  const categoryOptions = [
    { value: '', label: '— No Category —' },
    ...categories.map(c => ({ value: c.id, label: c.name })),
  ];

  function validate() {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = 'Required';
    if (!form.slug.trim()) e.slug = 'Required';
    if (!form.base_price || isNaN(Number(form.base_price))) e.base_price = 'Valid price required';
    variants.filter(v => !v._deleted).forEach((v, i) => {
      if (!v.name.trim()) e[`variant_name_${i}`] = 'Required';
      if (!v.sku.trim()) e[`variant_sku_${i}`] = 'Required';
      if (!v.price || isNaN(Number(v.price))) e[`variant_price_${i}`] = 'Valid price';
    });
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleImageUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    setUploadingIdx(imageUrls.length);
    try {
      const results = await Promise.all(files.map(f => uploadsApi.uploadImage(f, 'products')));
      setImageUrls(prev => [...prev, ...results.map(r => r.url)]);
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Image upload failed. Check R2 configuration.');
    } finally {
      setUploadingIdx(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  function removeImage(idx: number) {
    setImageUrls(prev => prev.filter((_, i) => i !== idx));
  }

  function moveImage(idx: number, dir: -1 | 1) {
    setImageUrls(prev => {
      const arr = [...prev];
      const swap = idx + dir;
      if (swap < 0 || swap >= arr.length) return arr;
      [arr[idx], arr[swap]] = [arr[swap], arr[idx]];
      return arr;
    });
  }

  function addVariant() {
    setVariants(prev => [...prev, {
      name: '',
      sku: '',
      price: form.base_price,
      stock_quantity: '0',
      display_order: prev.length,
      is_active: true,
    }]);
  }

  function removeVariant(idx: number) {
    setVariants(prev => prev.map((v, i) => i === idx ? { ...v, _deleted: true } : v));
  }

  function updateVariant(idx: number, field: keyof VariantRow, value: string | boolean) {
    setVariants(prev => prev.map((v, i) => i === idx ? { ...v, [field]: value } : v));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setSaving(true);
    setApiError('');

    const activeVariants = variants.filter(v => !v._deleted);
    const payload = {
      name: form.name.trim(),
      slug: form.slug.trim(),
      category_id: form.category_id || null,
      description: form.description.trim() || null,
      base_price: Number(form.base_price),
      image_urls: imageUrls,
      is_featured: form.is_featured,
      is_active: form.is_active,
      display_order: Number(form.display_order) || 0,
    };

    try {
      if (isEdit) {
        // Update product fields
        await productsApi.update(product.slug, payload);

        // Handle variants: update existing, create new, delete removed
        const deleted = variants.filter(v => v._deleted && v.id);
        const updated = variants.filter(v => !v._deleted && v.id);
        const created = variants.filter(v => !v._deleted && !v.id);

        await Promise.all([
          ...deleted.map(v => productsApi.deleteVariant(v.id!)),
          ...updated.map(v => productsApi.updateVariant(v.id!, {
            name: v.name, sku: v.sku,
            price: Number(v.price),
            stock_quantity: Number(v.stock_quantity),
            is_active: v.is_active,
            display_order: v.display_order,
          })),
          ...created.map(v => productsApi.addVariant(payload.slug, {
            name: v.name, sku: v.sku,
            price: Number(v.price),
            stock_quantity: Number(v.stock_quantity),
            display_order: v.display_order,
          })),
        ]);
      } else {
        // Create product with variants inline
        await productsApi.create({
          ...payload,
          variants: activeVariants.map(v => ({
            name: v.name, sku: v.sku,
            price: Number(v.price),
            stock_quantity: Number(v.stock_quantity),
            display_order: v.display_order,
          })),
        });
      }

      router.push('/products');
      router.refresh();
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Failed to save product.');
    } finally {
      setSaving(false);
    }
  }

  const activeVariants = variants.filter(v => !v._deleted);

  return (
    <form onSubmit={handleSubmit} className="space-y-8">
      {apiError && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-4 py-3">
          {apiError}
        </div>
      )}

      {/* Basic Info */}
      <section className="bg-white border border-gray-200 p-5">
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-4">Basic Information</h2>
        <div className="grid sm:grid-cols-2 gap-4">
          <Input
            label="Product Name"
            value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            error={errors.name}
          />
          <Input
            label="Slug (URL)"
            value={form.slug}
            onChange={e => setForm(f => ({ ...f, slug: e.target.value }))}
            error={errors.slug}
            helper="Auto-generated from name"
          />
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Select
            label="Category"
            value={form.category_id}
            onChange={e => setForm(f => ({ ...f, category_id: e.target.value }))}
            options={categoryOptions}
          />
          <Input
            label="Base Price (AED)"
            type="number"
            min="0"
            step="0.01"
            value={form.base_price}
            onChange={e => setForm(f => ({ ...f, base_price: e.target.value }))}
            error={errors.base_price}
          />
        </div>
        <div className="mt-4">
          <Textarea
            label="Description"
            value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            rows={3}
            placeholder="Product description..."
          />
        </div>
        <div className="flex gap-6 mt-4">
          <label className="flex items-center gap-2 cursor-pointer text-xs font-body text-gray-600 uppercase tracking-wider">
            <input
              type="checkbox"
              checked={form.is_featured}
              onChange={e => setForm(f => ({ ...f, is_featured: e.target.checked }))}
              className="accent-primary"
            />
            Featured
          </label>
          <label className="flex items-center gap-2 cursor-pointer text-xs font-body text-gray-600 uppercase tracking-wider">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))}
              className="accent-primary"
            />
            Active
          </label>
          <div className="w-24">
            <Input
              label="Order"
              type="number"
              min="0"
              value={form.display_order}
              onChange={e => setForm(f => ({ ...f, display_order: e.target.value }))}
            />
          </div>
        </div>
      </section>

      {/* Images */}
      <section className="bg-white border border-gray-200 p-5">
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-4">Images</h2>

        <div className="flex flex-wrap gap-3 mb-4">
          {imageUrls.map((url, idx) => (
            <div key={url + idx} className="relative group">
              <div className="relative w-24 h-24 border border-gray-200">
                <Image src={url} alt="" fill sizes="96px" className="object-cover" />
                {idx === 0 && (
                  <span className="absolute top-0 left-0 bg-primary text-white text-[9px] px-1 py-0.5 uppercase tracking-wide font-body">
                    Cover
                  </span>
                )}
              </div>
              <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-1">
                {idx > 0 && (
                  <button
                    type="button"
                    onClick={() => moveImage(idx, -1)}
                    className="text-white hover:text-secondary"
                    title="Move left"
                  >
                    <span className="material-icons text-[16px]">chevron_left</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => removeImage(idx)}
                  className="text-white hover:text-red-300"
                  title="Remove"
                >
                  <span className="material-icons text-[16px]">delete</span>
                </button>
                {idx < imageUrls.length - 1 && (
                  <button
                    type="button"
                    onClick={() => moveImage(idx, 1)}
                    className="text-white hover:text-secondary"
                    title="Move right"
                  >
                    <span className="material-icons text-[16px]">chevron_right</span>
                  </button>
                )}
              </div>
            </div>
          ))}

          {/* Upload button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadingIdx !== null}
            className="w-24 h-24 border-2 border-dashed border-gray-300 hover:border-primary flex flex-col items-center justify-center gap-1 text-gray-400 hover:text-primary transition-colors disabled:opacity-50"
          >
            {uploadingIdx !== null ? (
              <div className="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <>
                <span className="material-icons text-[20px]">add_photo_alternate</span>
                <span className="text-[10px] font-body uppercase tracking-wide">Add Image</span>
              </>
            )}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            multiple
            className="hidden"
            onChange={handleImageUpload}
          />
        </div>
        <p className="text-[11px] text-gray-400 font-body">
          JPEG, PNG, WebP · Max 5 MB per image · First image is the cover
        </p>
      </section>

      {/* Variants */}
      <section className="bg-white border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500">Variants</h2>
          <Button type="button" variant="ghost" size="sm" onClick={addVariant}>
            <span className="material-icons text-[14px]">add</span>
            Add Variant
          </Button>
        </div>

        {activeVariants.length === 0 ? (
          <p className="text-xs text-gray-400 font-body py-4 text-center border border-dashed border-gray-200">
            No variants yet. Add at least one variant with stock quantity.
          </p>
        ) : (
          <div className="space-y-3">
            {variants.map((v, idx) => {
              if (v._deleted) return null;
              const visibleIdx = variants.filter((vv, ii) => !vv._deleted && ii <= idx).length - 1;
              return (
                <div key={idx} className="grid grid-cols-2 sm:grid-cols-5 gap-2 items-end p-3 border border-gray-200 bg-gray-50">
                  <Input
                    label="Name"
                    value={v.name}
                    onChange={e => updateVariant(idx, 'name', e.target.value)}
                    error={errors[`variant_name_${visibleIdx}`]}
                    placeholder="e.g. 6 Pieces"
                  />
                  <Input
                    label="SKU"
                    value={v.sku}
                    onChange={e => updateVariant(idx, 'sku', e.target.value)}
                    error={errors[`variant_sku_${visibleIdx}`]}
                    placeholder="e.g. PROD-6"
                  />
                  <Input
                    label="Price (AED)"
                    type="number"
                    min="0"
                    step="0.01"
                    value={v.price}
                    onChange={e => updateVariant(idx, 'price', e.target.value)}
                    error={errors[`variant_price_${visibleIdx}`]}
                  />
                  <Input
                    label="Stock"
                    type="number"
                    min="0"
                    value={v.stock_quantity}
                    onChange={e => updateVariant(idx, 'stock_quantity', e.target.value)}
                  />
                  <div className="flex items-end gap-2">
                    {v.id && (
                      <label className="flex items-center gap-1 cursor-pointer text-[10px] text-gray-500 uppercase tracking-wide font-body pb-0.5">
                        <input
                          type="checkbox"
                          checked={v.is_active}
                          onChange={e => updateVariant(idx, 'is_active', e.target.checked)}
                          className="accent-primary"
                        />
                        Active
                      </label>
                    )}
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      onClick={() => removeVariant(idx)}
                    >
                      <span className="material-icons text-[14px]">delete</span>
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Actions */}
      <div className="flex items-center gap-3">
        <Button type="submit" loading={saving}>
          {isEdit ? 'Save Changes' : 'Create Product'}
        </Button>
        <Button type="button" variant="ghost" onClick={() => router.back()}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
