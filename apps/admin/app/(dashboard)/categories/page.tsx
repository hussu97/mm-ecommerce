'use client';

import { useEffect, useState } from 'react';
import { categoriesApi, uploadsApi, ApiError } from '@/lib/api';
import type { Category } from '@/lib/types';
import { Badge, Button, Input, Textarea } from '@/components/ui';

const BLANK = { name: '', slug: '', description: '', image_url: '', display_order: 0 };

export default function CategoriesPage() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editSlug, setEditSlug] = useState<string | null>(null);
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);
  const [reorderingSlug, setReorderingSlug] = useState<string | null>(null);
  const [apiError, setApiError] = useState('');
  const [uploadingImage, setUploadingImage] = useState(false);

  useEffect(() => {
    categoriesApi.list(true)
      .then(cats => setCategories([...cats].sort((a, b) => a.display_order - b.display_order)))
      .finally(() => setLoading(false));
  }, []);

  // Auto-slug from name on create
  useEffect(() => {
    if (!editSlug) {
      const slug = form.name.toLowerCase().trim()
        .replace(/[^\w\s-]/g, '').replace(/[\s_-]+/g, '-').replace(/^-+|-+$/g, '');
      setForm(f => ({ ...f, slug }));
    }
  }, [form.name, editSlug]);

  function openAdd() {
    setEditSlug(null);
    setForm({ ...BLANK, display_order: categories.length });
    setErrors({});
    setApiError('');
    setShowForm(true);
  }

  function openEdit(cat: Category) {
    setEditSlug(cat.slug);
    setForm({
      name: cat.name,
      slug: cat.slug,
      description: cat.description ?? '',
      image_url: cat.image_url ?? '',
      display_order: cat.display_order,
    });
    setErrors({});
    setApiError('');
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditSlug(null);
  }

  function validate() {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = 'Required';
    if (!form.slug.trim()) e.slug = 'Required';
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleImageUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingImage(true);
    try {
      const res = await uploadsApi.uploadImage(file, 'categories');
      setForm(f => ({ ...f, image_url: res.url }));
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Image upload failed.');
    } finally {
      setUploadingImage(false);
      e.target.value = '';
    }
  }

  async function handleSave() {
    if (!validate()) return;
    setSaving(true);
    setApiError('');
    try {
      const data = {
        name: form.name.trim(),
        slug: form.slug.trim(),
        description: form.description.trim() || null,
        image_url: form.image_url.trim() || null,
        display_order: form.display_order,
      };
      if (editSlug) {
        const updated = await categoriesApi.update(editSlug, data);
        setCategories(prev =>
          [...prev.map(c => c.slug === editSlug ? updated : c)].sort((a, b) => a.display_order - b.display_order)
        );
      } else {
        const created = await categoriesApi.create(data);
        setCategories(prev => [...prev, created].sort((a, b) => a.display_order - b.display_order));
      }
      closeForm();
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Failed to save category.');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(slug: string, name: string) {
    if (!confirm(`Delete "${name}"? This will fail if products are assigned to it.`)) return;
    setDeletingSlug(slug);
    try {
      await categoriesApi.delete(slug);
      setCategories(prev => prev.filter(c => c.slug !== slug));
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Delete failed.');
    } finally {
      setDeletingSlug(null);
    }
  }

  async function handleReorder(slug: string, dir: -1 | 1) {
    const sorted = [...categories].sort((a, b) => a.display_order - b.display_order);
    const idx = sorted.findIndex(c => c.slug === slug);
    const swapIdx = idx + dir;
    if (swapIdx < 0 || swapIdx >= sorted.length) return;

    const target = sorted[idx];
    const swapped = sorted[swapIdx];
    setReorderingSlug(slug);

    try {
      const [updatedTarget, updatedSwap] = await Promise.all([
        categoriesApi.update(target.slug, { display_order: swapped.display_order }),
        categoriesApi.update(swapped.slug, { display_order: target.display_order }),
      ]);
      setCategories(prev => {
        const next = prev.map(c => {
          if (c.slug === updatedTarget.slug) return updatedTarget;
          if (c.slug === updatedSwap.slug) return updatedSwap;
          return c;
        });
        return [...next].sort((a, b) => a.display_order - b.display_order);
      });
    } catch {
      // ignore
    } finally {
      setReorderingSlug(null);
    }
  }

  async function handleToggleActive(cat: Category) {
    try {
      const updated = await categoriesApi.update(cat.slug, { is_active: !cat.is_active });
      setCategories(prev => prev.map(c => c.slug === cat.slug ? updated : c));
    } catch {
      // silent
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-display text-2xl text-gray-800">Categories</h1>
        {!showForm && (
          <Button onClick={openAdd}>
            <span className="material-icons text-[14px]">add</span>
            New Category
          </Button>
        )}
      </div>

      {/* Form */}
      {showForm && (
        <div className="bg-white border border-primary/30 p-5 mb-6">
          <h2 className="text-xs font-body uppercase tracking-widest text-primary mb-4">
            {editSlug ? 'Edit Category' : 'New Category'}
          </h2>
          {apiError && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-3 py-2 mb-4">
              {apiError}
            </div>
          )}
          <div className="grid sm:grid-cols-2 gap-4">
            <Input
              label="Name"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              error={errors.name}
            />
            <Input
              label="Slug"
              value={form.slug}
              onChange={e => setForm(f => ({ ...f, slug: e.target.value }))}
              error={errors.slug}
            />
          </div>
          <div className="mt-4">
            <Textarea
              label="Description (optional)"
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              rows={2}
            />
          </div>
          <div className="mt-4 flex items-end gap-3">
            <div className="flex-1">
              <Input
                label="Image URL"
                value={form.image_url}
                onChange={e => setForm(f => ({ ...f, image_url: e.target.value }))}
                placeholder="https://... or upload below"
              />
            </div>
            <label className="shrink-0">
              <Button type="button" variant="ghost" size="sm" loading={uploadingImage} className="cursor-pointer">
                <span className="material-icons text-[14px]">upload</span>
                Upload
              </Button>
              <input type="file" accept="image/*" className="hidden" onChange={handleImageUpload} />
            </label>
          </div>
          <div className="flex gap-3 mt-5">
            <Button onClick={handleSave} loading={saving}>
              {editSlug ? 'Save Changes' : 'Create Category'}
            </Button>
            <Button variant="ghost" onClick={closeForm} disabled={saving}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="space-y-2">
          {[1,2,3,4].map(i => <div key={i} className="h-12 bg-gray-100 animate-pulse" />)}
        </div>
      ) : (
        <div className="bg-white border border-gray-200">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 w-8">Order</th>
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Name</th>
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Products</th>
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Status</th>
                <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {categories.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-sm text-gray-400 font-body">No categories yet.</td>
                </tr>
              ) : (
                categories.map((cat, idx) => (
                  <tr key={cat.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-2.5">
                      <div className="flex flex-col gap-0.5">
                        <button
                          onClick={() => handleReorder(cat.slug, -1)}
                          disabled={idx === 0 || reorderingSlug === cat.slug}
                          className="text-gray-300 hover:text-primary disabled:opacity-30 transition-colors"
                        >
                          <span className="material-icons text-[14px]">arrow_drop_up</span>
                        </button>
                        <button
                          onClick={() => handleReorder(cat.slug, 1)}
                          disabled={idx === categories.length - 1 || reorderingSlug === cat.slug}
                          className="text-gray-300 hover:text-primary disabled:opacity-30 transition-colors"
                        >
                          <span className="material-icons text-[14px]">arrow_drop_down</span>
                        </button>
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="font-body font-medium text-gray-800">{cat.name}</div>
                      <div className="text-[11px] text-gray-400 font-body">{cat.slug}</div>
                    </td>
                    <td className="px-4 py-2.5 text-center hidden sm:table-cell">
                      <span className="text-xs font-body text-gray-500">{cat.product_count}</span>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <button onClick={() => handleToggleActive(cat)}>
                        <Badge variant={cat.is_active ? 'success' : 'neutral'}>
                          {cat.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                      </button>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Button variant="ghost" size="sm" onClick={() => openEdit(cat)}>Edit</Button>
                        <Button
                          variant="danger"
                          size="sm"
                          loading={deletingSlug === cat.slug}
                          onClick={() => handleDelete(cat.slug, cat.name)}
                        >
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
