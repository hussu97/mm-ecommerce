'use client';

import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { categoriesApi, productsApi, modifiersApi, uploadsApi, ApiError } from '@/lib/api';
import type { Category, Modifier, Product } from '@/lib/types';
import { Button, Input, Select, Textarea } from '@/components/ui';
import { TranslationFields } from '@/components/TranslationFields';
import { useLanguages } from '@/hooks/useLanguages';
import { slugify } from '@/lib/utils';

interface Props {
  product?: Product;
}

export function ProductForm({ product }: Props) {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { languages } = useLanguages();

  const [categories, setCategories] = useState<Category[]>([]);
  const [form, setForm] = useState({
    name: product?.name ?? '',
    slug: product?.slug ?? '',
    sku: product?.sku ?? '',
    category_id: product?.category_id ?? '',
    description: product?.description ?? '',
    base_price: String(product?.base_price ?? '0'),
    calories: String(product?.calories ?? ''),
    preparation_time: String(product?.preparation_time ?? ''),
    is_featured: product?.is_featured ?? false,
    is_active: product?.is_active ?? true,
    is_stock_product: product?.is_stock_product ?? false,
    display_order: String(product?.display_order ?? 0),
  });
  const [translations, setTranslations] = useState<Record<string, Record<string, string>>>(product?.translations ?? {});
  const [imageUrls, setImageUrls] = useState<string[]>(product?.image_urls ?? []);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [uploadingIdx, setUploadingIdx] = useState<number | null>(null);
  const [apiError, setApiError] = useState('');

  // Modifier linking state
  const [allModifiers, setAllModifiers] = useState<Modifier[]>([]);
  const [currentProduct, setCurrentProduct] = useState<Product | undefined>(product);
  const [showLinkForm, setShowLinkForm] = useState(false);
  const [linkForm, setLinkForm] = useState({ modifier_id: '', minimum_options: 0, maximum_options: 1, free_options: 0, unique_options: false });
  const [linking, setLinking] = useState(false);
  const [unlinkingId, setUnlinkingId] = useState<string | null>(null);
  const [modifierError, setModifierError] = useState('');

  const isEdit = !!product;
  useEffect(() => {
    if (!isEdit) setForm(f => ({ ...f, slug: slugify(f.name) }));
  }, [form.name, isEdit]);

  useEffect(() => {
    categoriesApi.list(true).then(setCategories).catch(() => {});
    if (isEdit) modifiersApi.list().then(setAllModifiers).catch(() => {});
  }, [isEdit]);

  const categoryOptions = [
    { value: '', label: '— No Category —' },
    ...categories.map(c => ({ value: c.id, label: c.name })),
  ];

  function validate() {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = 'Required';
    if (!form.slug.trim()) e.slug = 'Required';
    if (form.base_price === '' || isNaN(Number(form.base_price))) e.base_price = 'Valid price required (0 or more)';
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setSaving(true);
    setApiError('');

    const payload = {
      name: form.name.trim(),
      slug: form.slug.trim(),
      sku: form.sku.trim() || null,
      category_id: form.category_id || null,
      description: form.description.trim() || null,
      translations: (() => {
        const t: Record<string, Record<string, string>> = {};
        for (const [lang, fields] of Object.entries(translations)) {
          const filtered: Record<string, string> = {};
          for (const [k, v] of Object.entries(fields)) {
            if (v.trim()) filtered[k] = v.trim();
          }
          if (Object.keys(filtered).length > 0) t[lang] = filtered;
        }
        return Object.keys(t).length > 0 ? t : null;
      })(),
      base_price: Number(form.base_price),
      calories: form.calories.trim() ? Number(form.calories) : null,
      preparation_time: form.preparation_time.trim() ? Number(form.preparation_time) : null,
      image_urls: imageUrls,
      is_featured: form.is_featured,
      is_active: form.is_active,
      is_stock_product: form.is_stock_product,
      display_order: Number(form.display_order) || 0,
    };

    try {
      if (isEdit) {
        await productsApi.update(product.slug, payload);
      } else {
        await productsApi.create(payload);
      }
      router.push('/products');
      router.refresh();
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Failed to save product.');
    } finally {
      setSaving(false);
    }
  }

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
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Input
            label="Slug (URL)"
            value={form.slug}
            onChange={e => setForm(f => ({ ...f, slug: e.target.value }))}
            error={errors.slug}
            helper="Auto-generated from name"
          />
          <Input
            label="SKU"
            value={form.sku}
            onChange={e => setForm(f => ({ ...f, sku: e.target.value }))}
            placeholder="Foodics SKU"
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
            helper="Set to 0 if price comes from modifier options"
          />
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Input
            label="Calories"
            type="number"
            min="0"
            value={form.calories}
            onChange={e => setForm(f => ({ ...f, calories: e.target.value }))}
            placeholder="Optional"
          />
          <Input
            label="Preparation Time (minutes)"
            type="number"
            min="0"
            value={form.preparation_time}
            onChange={e => setForm(f => ({ ...f, preparation_time: e.target.value }))}
            placeholder="Optional"
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
        <TranslationFields
          languages={languages}
          fields={[
            { key: 'name', label: 'Name' },
            { key: 'description', label: 'Description', type: 'textarea' },
          ]}
          translations={translations}
          onChange={setTranslations}
        />
        <div className="flex flex-wrap gap-6 mt-4">
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
          <label className="flex items-center gap-2 cursor-pointer text-xs font-body text-gray-600 uppercase tracking-wider">
            <input
              type="checkbox"
              checked={form.is_stock_product}
              onChange={e => setForm(f => ({ ...f, is_stock_product: e.target.checked }))}
              className="accent-primary"
            />
            Track Stock
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
                  <button type="button" onClick={() => moveImage(idx, -1)} className="text-white hover:text-secondary" title="Move left">
                    <span className="material-icons text-[16px]">chevron_left</span>
                  </button>
                )}
                <button type="button" onClick={() => removeImage(idx)} className="text-white hover:text-red-300" title="Remove">
                  <span className="material-icons text-[16px]">delete</span>
                </button>
                {idx < imageUrls.length - 1 && (
                  <button type="button" onClick={() => moveImage(idx, 1)} className="text-white hover:text-secondary" title="Move right">
                    <span className="material-icons text-[16px]">chevron_right</span>
                  </button>
                )}
              </div>
            </div>
          ))}

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

      {/* Modifiers (interactive, edit mode only) */}
      {isEdit && (
        <section className="bg-white border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xs font-body uppercase tracking-widest text-gray-500">
              Linked Modifiers ({(currentProduct ?? product).product_modifiers.length})
            </h2>
            {!showLinkForm && (
              <Button type="button" size="sm" variant="ghost" onClick={() => { setShowLinkForm(true); setModifierError(''); }}>
                <span className="material-icons text-[14px]">add</span>
                Link Modifier
              </Button>
            )}
          </div>

          {/* Link form */}
          {showLinkForm && (
            <div className="border border-gray-200 bg-gray-50 p-4 mb-4">
              {modifierError && <p className="text-xs text-red-500 mb-3">{modifierError}</p>}
              <div className="grid sm:grid-cols-2 gap-3 mb-3">
                <div>
                  <label className="text-[11px] font-body uppercase tracking-widest text-gray-500 block mb-1">Modifier</label>
                  <select
                    value={linkForm.modifier_id}
                    onChange={e => setLinkForm(f => ({ ...f, modifier_id: e.target.value }))}
                    className="w-full border border-gray-300 text-xs font-body px-2 py-1.5 bg-white focus:outline-none focus:border-primary"
                  >
                    <option value="">— Select modifier —</option>
                    {allModifiers
                      .filter(m => !(currentProduct ?? product).product_modifiers.some(pm => pm.modifier_id === m.id))
                      .map(m => <option key={m.id} value={m.id}>{m.name} ({m.reference})</option>)
                    }
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[11px] font-body uppercase tracking-widest text-gray-500 block mb-1">Min</label>
                    <input type="number" min="0" value={linkForm.minimum_options}
                      onChange={e => setLinkForm(f => ({ ...f, minimum_options: Number(e.target.value) }))}
                      className="w-full border border-gray-300 text-xs font-body px-2 py-1.5 focus:outline-none focus:border-primary"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-body uppercase tracking-widest text-gray-500 block mb-1">Max</label>
                    <input type="number" min="0" value={linkForm.maximum_options}
                      onChange={e => setLinkForm(f => ({ ...f, maximum_options: Number(e.target.value) }))}
                      className="w-full border border-gray-300 text-xs font-body px-2 py-1.5 focus:outline-none focus:border-primary"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-body uppercase tracking-widest text-gray-500 block mb-1">Free</label>
                    <input type="number" min="0" value={linkForm.free_options}
                      onChange={e => setLinkForm(f => ({ ...f, free_options: Number(e.target.value) }))}
                      className="w-full border border-gray-300 text-xs font-body px-2 py-1.5 focus:outline-none focus:border-primary"
                    />
                  </div>
                  <label className="flex items-center gap-2 text-xs font-body text-gray-600 pt-4">
                    <input type="checkbox" checked={linkForm.unique_options}
                      onChange={e => setLinkForm(f => ({ ...f, unique_options: e.target.checked }))}
                      className="accent-primary"
                    />
                    Unique
                  </label>
                </div>
              </div>
              <div className="flex gap-2">
                <Button type="button" size="sm" loading={linking} onClick={async () => {
                  if (!linkForm.modifier_id) { setModifierError('Select a modifier.'); return; }
                  setLinking(true); setModifierError('');
                  try {
                    const updated = await productsApi.linkModifier(product.slug, {
                      modifier_id: linkForm.modifier_id,
                      minimum_options: linkForm.minimum_options,
                      maximum_options: linkForm.maximum_options,
                      free_options: linkForm.free_options,
                      unique_options: linkForm.unique_options,
                    });
                    setCurrentProduct(updated);
                    setShowLinkForm(false);
                    setLinkForm({ modifier_id: '', minimum_options: 0, maximum_options: 1, free_options: 0, unique_options: false });
                  } catch (err) {
                    setModifierError(err instanceof ApiError ? err.message : 'Failed to link modifier.');
                  } finally {
                    setLinking(false);
                  }
                }}>Link</Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setShowLinkForm(false)}>Cancel</Button>
              </div>
            </div>
          )}

          {/* Linked modifiers list */}
          <div className="space-y-3">
            {(currentProduct ?? product).product_modifiers.map(pm => (
              <div key={pm.id} className="border border-gray-200 p-3 bg-gray-50">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-body font-medium text-gray-700">{pm.modifier.name} <span className="text-gray-400 font-normal">({pm.modifier.reference})</span></span>
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] text-gray-400 font-body">
                      min {pm.minimum_options} · max {pm.maximum_options}
                      {pm.free_options > 0 ? ` · ${pm.free_options} free` : ''}
                      {' · '}{pm.unique_options ? 'unique' : 'multi-qty'}
                    </span>
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      loading={unlinkingId === pm.modifier_id}
                      onClick={async () => {
                        if (!confirm(`Unlink "${pm.modifier.name}" from this product?`)) return;
                        setUnlinkingId(pm.modifier_id);
                        try {
                          const updated = await productsApi.unlinkModifier(product.slug, pm.modifier_id);
                          setCurrentProduct(updated);
                        } catch (err) {
                          alert(err instanceof ApiError ? err.message : 'Failed to unlink.');
                        } finally {
                          setUnlinkingId(null);
                        }
                      }}
                    >
                      Unlink
                    </Button>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1">
                  {pm.modifier.options.filter(o => o.is_active).map(opt => (
                    <span key={opt.id} className="text-[11px] bg-white border border-gray-200 px-2 py-0.5 font-body text-gray-600">
                      {opt.name}{opt.price > 0 ? ` +${Number(opt.price).toFixed(2)}` : ''}
                    </span>
                  ))}
                </div>
              </div>
            ))}
            {(currentProduct ?? product).product_modifiers.length === 0 && (
              <p className="text-xs text-gray-400 font-body">No modifiers linked.</p>
            )}
          </div>
        </section>
      )}

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
