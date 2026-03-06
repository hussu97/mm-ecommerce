'use client';

import { useEffect, useState } from 'react';
import { languagesApi, ApiError } from '@/lib/api';
import type { Language } from '@/lib/types';
import { Button, Input, Badge, Spinner, Pagination } from '@/components/ui';

type FormData = { code: string; name: string; native_name: string; direction: 'ltr' | 'rtl'; is_active: boolean };
const BLANK: FormData = { code: '', name: '', native_name: '', direction: 'ltr', is_active: true };

export default function LanguagesPage() {
  const [languages, setLanguages] = useState<Language[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [showForm, setShowForm] = useState(false);
  const [editCode, setEditCode] = useState<string | null>(null);
  const [form, setForm] = useState<FormData>(BLANK);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [actionCode, setActionCode] = useState<string | null>(null);
  const [apiError, setApiError] = useState('');

  useEffect(() => {
    languagesApi.listAll()
      .then(langs => setLanguages([...langs].sort((a, b) => a.display_order - b.display_order)))
      .finally(() => setLoading(false));
  }, []);

  const totalPages = Math.max(1, Math.ceil(languages.length / perPage));
  const paginated = languages.slice((page - 1) * perPage, page * perPage);

  function openAdd() {
    setEditCode(null);
    setForm({ ...BLANK });
    setErrors({});
    setApiError('');
    setShowForm(true);
  }

  function openEdit(lang: Language) {
    setEditCode(lang.code);
    setForm({ code: lang.code, name: lang.name, native_name: lang.native_name, direction: lang.direction, is_active: lang.is_active });
    setErrors({});
    setApiError('');
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditCode(null);
  }

  function validate() {
    const e: Record<string, string> = {};
    if (!form.code.trim()) e.code = 'Required';
    if (!form.name.trim()) e.name = 'Required';
    if (!form.native_name.trim()) e.native_name = 'Required';
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSave() {
    if (!validate()) return;
    setSaving(true);
    setApiError('');
    try {
      const data = {
        code: form.code.trim().toLowerCase(),
        name: form.name.trim(),
        native_name: form.native_name.trim(),
        direction: form.direction,
        is_active: form.is_active,
      };
      if (editCode) {
        const updated = await languagesApi.update(editCode, data);
        setLanguages(prev => [...prev.map(l => l.code === editCode ? updated : l)].sort((a, b) => a.display_order - b.display_order));
      } else {
        const created = await languagesApi.create(data);
        setLanguages(prev => [...prev, created].sort((a, b) => a.display_order - b.display_order));
      }
      closeForm();
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Failed to save language.');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(code: string, name: string) {
    if (!confirm(`Delete language "${name}"? This cannot be undone.`)) return;
    setActionCode(code);
    try {
      await languagesApi.delete(code);
      setLanguages(prev => prev.filter(l => l.code !== code));
      setPage(1);
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Delete failed.');
    } finally {
      setActionCode(null);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-display text-2xl text-gray-800">Languages</h1>
        {!showForm && (
          <Button onClick={openAdd}>
            <span className="material-icons text-[14px]">add</span>
            New Language
          </Button>
        )}
      </div>

      {/* Form */}
      {showForm && (
        <div className="bg-white border border-primary/30 p-5 mb-6">
          <h2 className="text-xs font-body uppercase tracking-widest text-primary mb-4">
            {editCode ? 'Edit Language' : 'New Language'}
          </h2>
          {apiError && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-3 py-2 mb-4">
              {apiError}
            </div>
          )}
          <div className="grid sm:grid-cols-3 gap-4">
            <Input
              label="Code"
              value={form.code}
              onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
              error={errors.code}
              placeholder="en, ar, fr…"
              disabled={!!editCode}
            />
            <Input label="Name" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} error={errors.name} placeholder="English" />
            <Input label="Native Name" value={form.native_name} onChange={e => setForm(f => ({ ...f, native_name: e.target.value }))} error={errors.native_name} placeholder="English" />
          </div>
          <div className="grid sm:grid-cols-2 gap-4 mt-4">
            <div>
              <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1">Direction</label>
              <select
                value={form.direction}
                onChange={e => setForm(f => ({ ...f, direction: e.target.value as 'ltr' | 'rtl' }))}
                className="w-full text-sm font-body border border-gray-300 bg-white px-3 py-2 rounded-sm outline-none focus:border-primary"
              >
                <option value="ltr">LTR (Left to Right)</option>
                <option value="rtl">RTL (Right to Left)</option>
              </select>
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))}
                  className="accent-primary"
                />
                <span className="text-sm font-body text-gray-700">Active</span>
              </label>
            </div>
          </div>
          <div className="flex gap-3 mt-5">
            <Button onClick={handleSave} loading={saving}>{editCode ? 'Save Changes' : 'Create Language'}</Button>
            <Button variant="ghost" onClick={closeForm} disabled={saving}>Cancel</Button>
          </div>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : (
        <div className="bg-white border border-gray-200">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Code</th>
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Name</th>
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Native Name</th>
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Direction</th>
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Status</th>
                <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {paginated.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-sm text-gray-400 font-body">No languages yet.</td>
                </tr>
              ) : (
                paginated.map(lang => (
                  <tr key={lang.code} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-2.5">
                      <span className="font-body font-medium text-gray-800">{lang.code}</span>
                    </td>
                    <td className="px-4 py-2.5 font-body text-gray-700">{lang.name}</td>
                    <td className="px-4 py-2.5 font-body text-gray-700 hidden sm:table-cell">{lang.native_name}</td>
                    <td className="px-4 py-2.5 text-center">
                      <Badge variant="neutral">{lang.direction.toUpperCase()}</Badge>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <div className="flex items-center justify-center gap-1.5">
                        {lang.is_default && <Badge variant="info">Default</Badge>}
                        <Badge variant={lang.is_active ? 'success' : 'warning'}>{lang.is_active ? 'Active' : 'Inactive'}</Badge>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Button variant="ghost" size="sm" onClick={() => openEdit(lang)}>Edit</Button>
                        {!lang.is_default && (
                          <Button variant="danger" size="sm" loading={actionCode === lang.code} onClick={() => handleDelete(lang.code, lang.name)}>Delete</Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={page}
        pages={totalPages}
        total={languages.length}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={p => { setPerPage(p); setPage(1); }}
        label="languages"
      />
    </div>
  );
}
