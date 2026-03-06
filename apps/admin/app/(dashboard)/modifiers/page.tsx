'use client';

import { Fragment, useEffect, useState } from 'react';
import { modifiersApi, bulkApi, ApiError } from '@/lib/api';
import type { Modifier, ModifierOption } from '@/lib/types';
import { Badge, Button, Input, Pagination, TabBar } from '@/components/ui';
import { TranslationFields } from '@/components/TranslationFields';
import { useLanguages } from '@/hooks/useLanguages';

const BLANK_MODIFIER = { reference: '', name: '' };
const BLANK_OPTION = { name: '', sku: '', price: '0', calories: '', is_active: true, display_order: '0' };

export default function ModifiersPage() {
  const { languages } = useLanguages();
  const [modifiers, setModifiers] = useState<Modifier[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active');

  // Modifier form
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState(BLANK_MODIFIER);
  const [formTranslations, setFormTranslations] = useState<Record<string, Record<string, string>>>({});
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  // Expanded row
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Per-modifier action
  const [actionId, setActionId] = useState<string | null>(null);

  // Search
  const [search, setSearch] = useState('');

  // Bulk selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulking, setBulking] = useState(false);

  // Pagination
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  // Option add form (per modifier)
  const [addingOptionFor, setAddingOptionFor] = useState<string | null>(null);
  const [optionForm, setOptionForm] = useState(BLANK_OPTION);
  const [optionTranslations, setOptionTranslations] = useState<Record<string, Record<string, string>>>({});
  const [savingOption, setSavingOption] = useState(false);
  const [optionError, setOptionError] = useState('');

  // Option edit
  const [editingOptionId, setEditingOptionId] = useState<string | null>(null);
  const [editOptionForm, setEditOptionForm] = useState(BLANK_OPTION);
  const [editOptionTranslations, setEditOptionTranslations] = useState<Record<string, Record<string, string>>>({});
  const [savingEditOption, setSavingEditOption] = useState(false);

  // Option delete
  const [deletingOptionId, setDeletingOptionId] = useState<string | null>(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      setModifiers(await modifiersApi.list(true));
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }

  const q = search.trim().toLowerCase();
  const filteredModifiers = modifiers.filter(m =>
    (activeTab === 'active' ? m.is_active : !m.is_active) &&
    (!q ||
      m.name.toLowerCase().includes(q) ||
      m.reference.toLowerCase().includes(q) ||
      (m.translations?.ar?.name ?? '').toLowerCase().includes(q) ||
      m.options.some(o => o.name.toLowerCase().includes(q) || o.sku.toLowerCase().includes(q))
    )
  );
  const modifierPages = Math.max(1, Math.ceil(filteredModifiers.length / perPage));
  const paginatedModifiers = filteredModifiers.slice((page - 1) * perPage, page * perPage);

  function openAdd() {
    setEditId(null);
    setForm(BLANK_MODIFIER);
    setFormTranslations({});
    setFormError('');
    setShowForm(true);
  }

  function openEdit(m: Modifier) {
    setEditId(m.id);
    setForm({ reference: m.reference, name: m.name });
    setFormTranslations(m.translations ?? {});
    setFormError('');
    setShowForm(true);
  }

  function cleanTranslations(t: Record<string, Record<string, string>>): Record<string, Record<string, string>> | null {
    const result: Record<string, Record<string, string>> = {};
    for (const [lang, fields] of Object.entries(t)) {
      const filtered: Record<string, string> = {};
      for (const [k, v] of Object.entries(fields)) {
        if (v.trim()) filtered[k] = v.trim();
      }
      if (Object.keys(filtered).length > 0) result[lang] = filtered;
    }
    return Object.keys(result).length > 0 ? result : null;
  }

  async function handleSave() {
    if (!form.reference.trim() || !form.name.trim()) { setFormError('Reference and Name are required.'); return; }
    setSaving(true); setFormError('');
    try {
      const data = { reference: form.reference.trim(), name: form.name.trim(), translations: cleanTranslations(formTranslations) };
      if (editId) {
        const updated = await modifiersApi.update(editId, data);
        setModifiers(prev => prev.map(m => m.id === editId ? updated : m));
      } else {
        const created = await modifiersApi.create(data);
        setModifiers(prev => [...prev, created]);
      }
      setShowForm(false);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  async function handleDeactivate(id: string, name: string) {
    if (!confirm(`Deactivate modifier "${name}"? It will move to the Inactive tab.`)) return;
    setActionId(id);
    try {
      await modifiersApi.delete(id);
      setModifiers(prev => prev.map(m => m.id === id ? { ...m, is_active: false } : m));
      if (expandedId === id) setExpandedId(null);
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Deactivate failed.');
    } finally {
      setActionId(null);
    }
  }

  async function handleRestore(id: string) {
    setActionId(id);
    try {
      const updated = await modifiersApi.update(id, { is_active: true });
      setModifiers(prev => prev.map(m => m.id === id ? updated : m));
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Restore failed.');
    } finally {
      setActionId(null);
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
    const allOnPage = paginatedModifiers.every(m => selectedIds.has(m.id));
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (allOnPage) {
        paginatedModifiers.forEach(m => next.delete(m.id));
      } else {
        paginatedModifiers.forEach(m => next.add(m.id));
      }
      return next;
    });
  }

  async function handleBulkStatus(is_active: boolean) {
    setBulking(true);
    try {
      await bulkApi.updateStatus('modifiers', Array.from(selectedIds), is_active);
      await load();
      setSelectedIds(new Set());
      setPage(1);
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Bulk action failed.');
    } finally {
      setBulking(false);
    }
  }

  function openAddOption(modifierId: string) {
    setAddingOptionFor(modifierId);
    setOptionForm(BLANK_OPTION);
    setOptionTranslations({});
    setOptionError('');
  }

  async function handleAddOption(modifierId: string) {
    if (!optionForm.name.trim() || !optionForm.sku.trim()) { setOptionError('Name and SKU are required.'); return; }
    setSavingOption(true); setOptionError('');
    try {
      const data = {
        name: optionForm.name.trim(),
        translations: cleanTranslations(optionTranslations),
        sku: optionForm.sku.trim(),
        price: Number(optionForm.price) || 0,
        calories: optionForm.calories ? Number(optionForm.calories) : null,
        is_active: optionForm.is_active,
        display_order: Number(optionForm.display_order) || 0,
      };
      const updated = await modifiersApi.addOption(modifierId, data);
      setModifiers(prev => prev.map(m => m.id === modifierId ? updated : m));
      setAddingOptionFor(null);
    } catch (err) {
      setOptionError(err instanceof ApiError ? err.message : 'Add option failed.');
    } finally {
      setSavingOption(false);
    }
  }

  function openEditOption(modifierId: string, opt: ModifierOption) {
    setEditingOptionId(opt.id);
    setEditOptionForm({
      name: opt.name,
      sku: opt.sku,
      price: String(opt.price),
      calories: opt.calories != null ? String(opt.calories) : '',
      is_active: opt.is_active,
      display_order: String(opt.display_order),
    });
    setEditOptionTranslations(opt.translations ?? {});
  }

  async function handleSaveOption(modifierId: string, optionId: string) {
    setSavingEditOption(true);
    try {
      const data = {
        name: editOptionForm.name.trim() || undefined,
        translations: cleanTranslations(editOptionTranslations),
        sku: editOptionForm.sku.trim() || undefined,
        price: editOptionForm.price !== '' ? Number(editOptionForm.price) : undefined,
        calories: editOptionForm.calories ? Number(editOptionForm.calories) : null,
        is_active: editOptionForm.is_active,
        display_order: Number(editOptionForm.display_order) || 0,
      };
      const updated = await modifiersApi.updateOption(modifierId, optionId, data);
      setModifiers(prev => prev.map(m => m.id === modifierId ? updated : m));
      setEditingOptionId(null);
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Update failed.');
    } finally {
      setSavingEditOption(false);
    }
  }

  async function handleDeactivateOption(modifierId: string, optionId: string, name: string) {
    if (!confirm(`Deactivate option "${name}"?`)) return;
    setDeletingOptionId(optionId);
    try {
      const updated = await modifiersApi.deleteOption(modifierId, optionId);
      setModifiers(prev => prev.map(m => m.id === modifierId ? updated : m));
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Deactivate failed.');
    } finally {
      setDeletingOptionId(null);
    }
  }

  const activeCount = modifiers.filter(m => m.is_active).length;
  const inactiveCount = modifiers.filter(m => !m.is_active).length;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Modifiers</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{modifiers.length} total</p>
        </div>
        {!showForm && (
          <Button onClick={openAdd}>
            <span className="material-icons text-[14px]">add</span>
            New Modifier
          </Button>
        )}
      </div>

      {/* Modifier form */}
      {showForm && (
        <div className="bg-white border border-primary/30 p-5 mb-6">
          <h2 className="text-xs font-body uppercase tracking-widest text-primary mb-4">
            {editId ? 'Edit Modifier' : 'New Modifier'}
          </h2>
          {formError && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-3 py-2 mb-4">{formError}</div>
          )}
          <div className="grid sm:grid-cols-2 gap-4">
            <Input
              label="Reference"
              value={form.reference}
              onChange={e => setForm(f => ({ ...f, reference: e.target.value }))}
              disabled={!!editId}
              placeholder="e.g. size"
            />
            <Input
              label="Name"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            />
          </div>
          <TranslationFields
            languages={languages}
            fields={[{ key: 'name', label: 'Name' }]}
            translations={formTranslations}
            onChange={setFormTranslations}
          />
          <div className="flex gap-3 mt-5">
            <Button onClick={handleSave} loading={saving}>{editId ? 'Save Changes' : 'Create Modifier'}</Button>
            <Button variant="ghost" onClick={() => setShowForm(false)} disabled={saving}>Cancel</Button>
          </div>
        </div>
      )}

      {/* Search */}
      <div className="mb-4 max-w-xs">
        <Input
          placeholder="Search by name, reference, or option…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1); }}
        />
      </div>

      {/* Tabs */}
      <TabBar
        tabs={[
          { key: 'active', label: 'Active', count: activeCount },
          { key: 'inactive', label: 'Inactive', count: inactiveCount },
        ]}
        active={activeTab}
        onChange={key => { setActiveTab(key as 'active' | 'inactive'); setSelectedIds(new Set()); setPage(1); setSearch(''); }}
      />

      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 bg-primary/10 border border-primary/30 px-4 py-2.5 mb-4">
          <span className="text-xs font-body text-primary font-medium">{selectedIds.size} selected</span>
          <button onClick={() => setSelectedIds(new Set(filteredModifiers.map(m => m.id)))} className="text-xs font-body text-gray-500 hover:text-primary underline">All</button>
          <button onClick={() => setSelectedIds(new Set())} className="text-xs font-body text-gray-500 hover:text-primary underline">None</button>
          <div className="flex-1" />
          <Button size="sm" loading={bulking} onClick={() => handleBulkStatus(true)}>Activate</Button>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkStatus(false)}>Deactivate</Button>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="space-y-2">{[1,2,3].map(i => <div key={i} className="h-12 bg-gray-100 animate-pulse" />)}</div>
      ) : (
        <div className="bg-white border border-gray-200">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={paginatedModifiers.length > 0 && paginatedModifiers.every(m => selectedIds.has(m.id))}
                    onChange={toggleSelectAll}
                    className="accent-primary"
                  />
                </th>
                <th className="px-4 py-3 w-6"></th>
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Name</th>
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Reference</th>
                <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Options</th>
                <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody>
              {paginatedModifiers.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-sm text-gray-400 font-body">No modifiers yet.</td>
                </tr>
              ) : paginatedModifiers.map(m => (
                <Fragment key={m.id}>
                  <tr className={`border-b border-gray-100 hover:bg-gray-50 transition-colors ${selectedIds.has(m.id) ? 'bg-primary/5' : ''}`}>
                    <td className="px-4 py-2.5 w-8">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(m.id)}
                        onChange={() => toggleSelect(m.id)}
                        className="accent-primary"
                      />
                    </td>
                    <td className="px-4 py-2.5">
                      <button
                        onClick={() => setExpandedId(expandedId === m.id ? null : m.id)}
                        className="text-gray-400 hover:text-primary transition-colors"
                      >
                        <span className="material-icons text-[16px]">
                          {expandedId === m.id ? 'expand_less' : 'expand_more'}
                        </span>
                      </button>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="font-body font-medium text-gray-800 text-sm">{m.name}</span>
                    </td>
                    <td className="px-4 py-2.5 hidden md:table-cell">
                      <span className="text-xs font-body text-gray-400">{m.reference}</span>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <span className="text-xs font-body text-gray-500">{m.options.length}</span>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {activeTab === 'active' ? (
                          <>
                            <Button variant="ghost" size="sm" onClick={() => openEdit(m)}>Edit</Button>
                            <Button
                              variant="danger"
                              size="sm"
                              loading={actionId === m.id}
                              onClick={() => handleDeactivate(m.id, m.name)}
                            >Deactivate</Button>
                          </>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            loading={actionId === m.id}
                            onClick={() => handleRestore(m.id)}
                          >Restore</Button>
                        )}
                      </div>
                    </td>
                  </tr>

                  {/* Expanded options */}
                  {expandedId === m.id && (
                    <tr key={`${m.id}-options`}>
                      <td colSpan={6} className="bg-gray-50 border-b border-gray-100">
                        <div className="px-8 py-4">
                          <div className="flex items-center justify-between mb-3">
                            <span className="text-[11px] font-body uppercase tracking-widest text-gray-500">Options</span>
                            {addingOptionFor !== m.id && (
                              <Button size="sm" variant="ghost" onClick={() => openAddOption(m.id)}>
                                <span className="material-icons text-[12px]">add</span>
                                Add Option
                              </Button>
                            )}
                          </div>

                          {/* Options table */}
                          {m.options.length > 0 && (
                            <table className="w-full text-xs mb-4">
                              <thead>
                                <tr className="border-b border-gray-200">
                                  <th className="py-1.5 text-left font-body uppercase tracking-widest text-gray-400 text-[10px]">Name</th>
                                  <th className="py-1.5 text-left font-body uppercase tracking-widest text-gray-400 text-[10px] hidden sm:table-cell">SKU</th>
                                  <th className="py-1.5 text-right font-body uppercase tracking-widest text-gray-400 text-[10px]">Price</th>
                                  <th className="py-1.5 text-center font-body uppercase tracking-widest text-gray-400 text-[10px] hidden md:table-cell">Calories</th>
                                  <th className="py-1.5 text-center font-body uppercase tracking-widest text-gray-400 text-[10px]">Active</th>
                                  <th className="py-1.5 text-center font-body uppercase tracking-widest text-gray-400 text-[10px] hidden sm:table-cell">Order</th>
                                  <th className="py-1.5 text-right font-body uppercase tracking-widest text-gray-400 text-[10px]">Actions</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-gray-100">
                                {m.options.map(opt => (
                                  editingOptionId === opt.id ? (
                                    <tr key={opt.id} className="bg-white">
                                      <td className="py-2 pr-2">
                                        <input value={editOptionForm.name} onChange={e => setEditOptionForm(f => ({ ...f, name: e.target.value }))}
                                          className="w-full border border-gray-300 text-xs font-body px-2 py-1 focus:outline-none focus:border-primary" />
                                      </td>
                                      <td className="py-2 pr-2 hidden sm:table-cell">
                                        <input value={editOptionForm.sku} onChange={e => setEditOptionForm(f => ({ ...f, sku: e.target.value }))}
                                          className="w-full border border-gray-300 text-xs font-body px-2 py-1 focus:outline-none focus:border-primary" />
                                      </td>
                                      <td className="py-2 pr-2">
                                        <input type="number" min="0" step="0.01" value={editOptionForm.price} onChange={e => setEditOptionForm(f => ({ ...f, price: e.target.value }))}
                                          className="w-20 border border-gray-300 text-xs font-body px-2 py-1 focus:outline-none focus:border-primary" />
                                      </td>
                                      <td className="py-2 pr-2 hidden md:table-cell">
                                        <input type="number" min="0" value={editOptionForm.calories} onChange={e => setEditOptionForm(f => ({ ...f, calories: e.target.value }))}
                                          className="w-16 border border-gray-300 text-xs font-body px-2 py-1 focus:outline-none focus:border-primary" placeholder="—" />
                                      </td>
                                      <td className="py-2 text-center">
                                        <input type="checkbox" checked={editOptionForm.is_active} onChange={e => setEditOptionForm(f => ({ ...f, is_active: e.target.checked }))} className="accent-primary" />
                                      </td>
                                      <td className="py-2 pr-2 hidden sm:table-cell">
                                        <input type="number" min="0" value={editOptionForm.display_order} onChange={e => setEditOptionForm(f => ({ ...f, display_order: e.target.value }))}
                                          className="w-12 border border-gray-300 text-xs font-body px-2 py-1 focus:outline-none focus:border-primary" />
                                      </td>
                                      <td className="py-2 text-right">
                                        <div className="flex items-center justify-end gap-1">
                                          <Button size="sm" loading={savingEditOption} onClick={() => handleSaveOption(m.id, opt.id)}>Save</Button>
                                          <Button size="sm" variant="ghost" onClick={() => setEditingOptionId(null)}>Cancel</Button>
                                        </div>
                                      </td>
                                    </tr>
                                  ) : (
                                    <tr key={opt.id} className="hover:bg-white/60 transition-colors">
                                      <td className="py-2 pr-2">
                                        <div className="font-body text-gray-700">{opt.name}</div>
                                        {opt.translations?.ar?.name && <div className="text-[10px] text-gray-400 font-body">{opt.translations.ar.name}</div>}
                                      </td>
                                      <td className="py-2 pr-2 font-body text-gray-400 hidden sm:table-cell">{opt.sku}</td>
                                      <td className="py-2 pr-2 text-right font-body text-gray-700">
                                        {opt.price > 0 ? `AED ${Number(opt.price).toFixed(2)}` : '—'}
                                      </td>
                                      <td className="py-2 text-center font-body text-gray-400 hidden md:table-cell">{opt.calories ?? '—'}</td>
                                      <td className="py-2 text-center">
                                        <Badge variant={opt.is_active ? 'success' : 'neutral'}>
                                          {opt.is_active ? 'Yes' : 'No'}
                                        </Badge>
                                      </td>
                                      <td className="py-2 text-center font-body text-gray-400 hidden sm:table-cell">{opt.display_order}</td>
                                      <td className="py-2 text-right">
                                        <div className="flex items-center justify-end gap-1">
                                          <Button size="sm" variant="ghost" onClick={() => openEditOption(m.id, opt)}>Edit</Button>
                                          <Button
                                            size="sm"
                                            variant="danger"
                                            loading={deletingOptionId === opt.id}
                                            onClick={() => handleDeactivateOption(m.id, opt.id, opt.name)}
                                          >Del</Button>
                                        </div>
                                      </td>
                                    </tr>
                                  )
                                ))}
                              </tbody>
                            </table>
                          )}

                          {/* Add option form */}
                          {addingOptionFor === m.id && (
                            <div className="border border-gray-200 bg-white p-4 mt-2">
                              {optionError && <p className="text-xs text-red-500 mb-3">{optionError}</p>}
                              <div className="grid sm:grid-cols-3 gap-3 mb-3">
                                <Input
                                  label="Name"
                                  value={optionForm.name}
                                  onChange={e => setOptionForm(f => ({ ...f, name: e.target.value }))}
                                />
                                <Input
                                  label="SKU"
                                  value={optionForm.sku}
                                  onChange={e => setOptionForm(f => ({ ...f, sku: e.target.value }))}
                                />
                                <Input
                                  label="Price (AED)"
                                  type="number"
                                  min="0"
                                  step="0.01"
                                  value={optionForm.price}
                                  onChange={e => setOptionForm(f => ({ ...f, price: e.target.value }))}
                                />
                                <Input
                                  label="Calories"
                                  type="number"
                                  min="0"
                                  value={optionForm.calories}
                                  onChange={e => setOptionForm(f => ({ ...f, calories: e.target.value }))}
                                  placeholder="Optional"
                                />
                                <Input
                                  label="Display Order"
                                  type="number"
                                  min="0"
                                  value={optionForm.display_order}
                                  onChange={e => setOptionForm(f => ({ ...f, display_order: e.target.value }))}
                                />
                              </div>
                              <TranslationFields
                                languages={languages}
                                fields={[{ key: 'name', label: 'Name' }]}
                                translations={optionTranslations}
                                onChange={setOptionTranslations}
                              />
                              <label className="flex items-center gap-2 text-xs font-body text-gray-600 mb-3 mt-3">
                                <input
                                  type="checkbox"
                                  checked={optionForm.is_active}
                                  onChange={e => setOptionForm(f => ({ ...f, is_active: e.target.checked }))}
                                  className="accent-primary"
                                />
                                Active
                              </label>
                              <div className="flex gap-2">
                                <Button size="sm" loading={savingOption} onClick={() => handleAddOption(m.id)}>Add Option</Button>
                                <Button size="sm" variant="ghost" onClick={() => setAddingOptionFor(null)}>Cancel</Button>
                              </div>
                            </div>
                          )}

                          {m.options.length === 0 && addingOptionFor !== m.id && (
                            <p className="text-xs text-gray-400 font-body">No options yet.</p>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={page}
        pages={modifierPages}
        total={filteredModifiers.length}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={p => { setPerPage(p); setPage(1); }}
        label="modifiers"
      />
    </div>
  );
}
