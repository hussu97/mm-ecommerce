'use client';

import { useEffect, useState } from 'react';
import { regionsApi, ApiError } from '@/lib/api';
import type { Region, DeliverySettings } from '@/lib/types';
import { Button, Input, Spinner } from '@/components/ui';
import { cn } from '@/lib/utils';

// ── Supported locales — kept in sync with DB language table ──────────────────
const LOCALES = [
  { code: 'en', label: 'English', dir: 'ltr' },
  { code: 'ar', label: 'Arabic', dir: 'rtl' },
];

// ── Region edit state ─────────────────────────────────────────────────────────

interface EditState {
  slug: string;
  name_translations: Record<string, string>;
  delivery_fee: string;
  is_active: boolean;
  sort_order: string;
}

function regionToEdit(r: Region): EditState {
  return {
    slug: r.slug,
    name_translations: { ...r.name_translations },
    delivery_fee: String(r.delivery_fee),
    is_active: r.is_active,
    sort_order: String(r.sort_order),
  };
}

// ── Main component ────────────────────────────────────────────────────────────

export default function RegionsPage() {
  const [regions, setRegions] = useState<Region[]>([]);
  const [settings, setSettings] = useState<DeliverySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Edit region dialog
  const [editing, setEditing] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState('');

  // Edit settings dialog
  const [editingSettings, setEditingSettings] = useState(false);
  const [settingsForm, setSettingsForm] = useState({ free_delivery_threshold: '', pickup_fee: '' });
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsError, setSettingsError] = useState('');

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [r, s] = await Promise.all([regionsApi.list(), regionsApi.getSettings()]);
      setRegions(r);
      setSettings(s);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load regions.');
    } finally {
      setLoading(false);
    }
  }

  // ── Region edit ────────────────────────────────────────────────────────────

  function openEdit(region: Region) {
    setEditing(regionToEdit(region));
    setEditError('');
  }

  function closeEdit() {
    setEditing(null);
    setEditError('');
  }

  async function saveRegion() {
    if (!editing) return;
    setSaving(true);
    setEditError('');
    try {
      const updated = await regionsApi.update(editing.slug, {
        name_translations: editing.name_translations,
        delivery_fee: parseFloat(editing.delivery_fee),
        is_active: editing.is_active,
        sort_order: parseInt(editing.sort_order, 10),
      });
      setRegions(prev => prev.map(r => r.slug === updated.slug ? updated : r));
      closeEdit();
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  // ── Settings edit ──────────────────────────────────────────────────────────

  function openSettings() {
    if (!settings) return;
    setSettingsForm({
      free_delivery_threshold: String(settings.free_delivery_threshold),
      pickup_fee: String(settings.pickup_fee),
    });
    setSettingsError('');
    setEditingSettings(true);
  }

  async function saveSettings() {
    setSavingSettings(true);
    setSettingsError('');
    try {
      const updated = await regionsApi.updateSettings({
        free_delivery_threshold: parseFloat(settingsForm.free_delivery_threshold),
        pickup_fee: parseFloat(settingsForm.pickup_fee),
      });
      setSettings(updated);
      setEditingSettings(false);
    } catch (err) {
      setSettingsError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSavingSettings(false);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Regions &amp; Delivery</h1>
          <p className="text-xs font-body text-gray-500 mt-1">
            Manage delivery regions, per-region fees, and global delivery settings.
          </p>
        </div>
      </div>

      {loading && <div className="flex justify-center py-12"><Spinner /></div>}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-4 py-3 mb-6">
          {error}
        </div>
      )}

      {!loading && !error && (
        <>
          {/* Global Settings Card */}
          {settings && (
            <div className="bg-white border border-gray-200 p-5 mb-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="font-body text-sm font-medium text-gray-700 uppercase tracking-widest">
                  Global Settings
                </h2>
                <Button size="sm" variant="outline" onClick={openSettings}>
                  Edit
                </Button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                <div>
                  <p className="text-[11px] font-body text-gray-400 uppercase tracking-widest mb-1">
                    Free delivery threshold
                  </p>
                  <p className="font-body text-sm text-gray-800">
                    AED {settings.free_delivery_threshold.toFixed(2)}
                  </p>
                </div>
                <div>
                  <p className="text-[11px] font-body text-gray-400 uppercase tracking-widest mb-1">
                    Pickup fee
                  </p>
                  <p className="font-body text-sm text-gray-800">
                    AED {settings.pickup_fee.toFixed(2)}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Regions Table */}
          <div className="bg-white border border-gray-200">
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-body">
                <thead>
                  <tr className="border-b border-gray-100">
                    <th className="text-left px-4 py-3 text-gray-400 uppercase tracking-widest font-medium">
                      Region
                    </th>
                    {LOCALES.map(l => (
                      <th
                        key={l.code}
                        className="text-left px-4 py-3 text-gray-400 uppercase tracking-widest font-medium"
                      >
                        {l.label}
                      </th>
                    ))}
                    <th className="text-left px-4 py-3 text-gray-400 uppercase tracking-widest font-medium">
                      Fee (AED)
                    </th>
                    <th className="text-left px-4 py-3 text-gray-400 uppercase tracking-widest font-medium">
                      Status
                    </th>
                    <th className="text-left px-4 py-3 text-gray-400 uppercase tracking-widest font-medium">
                      Order
                    </th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {regions.map(region => (
                    <tr key={region.slug} className="border-b border-gray-50 hover:bg-gray-50/50">
                      <td className="px-4 py-3 text-gray-500 font-mono text-[11px]">
                        {region.slug}
                      </td>
                      {LOCALES.map(l => (
                        <td key={l.code} className={cn('px-4 py-3 text-gray-800', l.dir === 'rtl' ? 'text-right' : '')}>
                          {region.name_translations[l.code] ?? '—'}
                        </td>
                      ))}
                      <td className="px-4 py-3 text-gray-800">
                        {region.delivery_fee.toFixed(2)}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            'inline-block px-2 py-0.5 text-[10px] uppercase tracking-widest',
                            region.is_active
                              ? 'bg-green-50 text-green-600'
                              : 'bg-gray-100 text-gray-400',
                          )}
                        >
                          {region.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-500">{region.sort_order}</td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => openEdit(region)}
                          className="text-primary hover:underline text-[11px] uppercase tracking-widest"
                        >
                          Edit
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* ── Edit Region Dialog ──────────────────────────────────────────────── */}
      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/30" onClick={closeEdit} />
          <div className="relative bg-white w-full max-w-md mx-4 p-6 shadow-xl">
            <h2 className="font-display text-lg text-gray-800 mb-5">
              Edit Region — <span className="font-mono text-sm">{editing.slug}</span>
            </h2>

            {/* Name translations */}
            <div className="space-y-3 mb-4">
              {LOCALES.map(l => (
                <div key={l.code}>
                  <label className="block text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">
                    Name ({l.label})
                  </label>
                  <Input
                    dir={l.dir}
                    value={editing.name_translations[l.code] ?? ''}
                    onChange={e =>
                      setEditing(prev =>
                        prev
                          ? {
                              ...prev,
                              name_translations: {
                                ...prev.name_translations,
                                [l.code]: e.target.value,
                              },
                            }
                          : prev
                      )
                    }
                    placeholder={`Region name in ${l.label}`}
                  />
                </div>
              ))}
            </div>

            {/* Delivery fee */}
            <div className="mb-4">
              <label className="block text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">
                Delivery Fee (AED)
              </label>
              <Input
                type="number"
                min="0"
                step="0.01"
                value={editing.delivery_fee}
                onChange={e => setEditing(prev => prev ? { ...prev, delivery_fee: e.target.value } : prev)}
              />
            </div>

            {/* Sort order */}
            <div className="mb-4">
              <label className="block text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">
                Display Order
              </label>
              <Input
                type="number"
                min="0"
                value={editing.sort_order}
                onChange={e => setEditing(prev => prev ? { ...prev, sort_order: e.target.value } : prev)}
              />
            </div>

            {/* Active toggle */}
            <div className="mb-5 flex items-center gap-3">
              <button
                type="button"
                role="switch"
                aria-checked={editing.is_active}
                onClick={() => setEditing(prev => prev ? { ...prev, is_active: !prev.is_active } : prev)}
                className={cn(
                  'relative w-10 h-5 rounded-full transition-colors focus:outline-none',
                  editing.is_active ? 'bg-primary' : 'bg-gray-200',
                )}
              >
                <span
                  className={cn(
                    'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform',
                    editing.is_active ? 'translate-x-5' : 'translate-x-0',
                  )}
                />
              </button>
              <span className="font-body text-xs text-gray-600">
                {editing.is_active ? 'Active (available for delivery)' : 'Inactive (hidden from customers)'}
              </span>
            </div>

            {editError && (
              <p className="text-red-500 text-xs mb-3">{editError}</p>
            )}

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={closeEdit} disabled={saving}>
                Cancel
              </Button>
              <Button onClick={saveRegion} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Edit Settings Dialog ────────────────────────────────────────────── */}
      {editingSettings && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/30" onClick={() => setEditingSettings(false)} />
          <div className="relative bg-white w-full max-w-sm mx-4 p-6 shadow-xl">
            <h2 className="font-display text-lg text-gray-800 mb-5">Global Delivery Settings</h2>

            <div className="mb-4">
              <label className="block text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">
                Free Delivery Threshold (AED)
              </label>
              <Input
                type="number"
                min="0"
                step="0.01"
                value={settingsForm.free_delivery_threshold}
                onChange={e => setSettingsForm(f => ({ ...f, free_delivery_threshold: e.target.value }))}
              />
              <p className="text-[11px] font-body text-gray-400 mt-1">
                Orders at or above this value get free delivery.
              </p>
            </div>

            <div className="mb-5">
              <label className="block text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">
                Pickup Fee (AED)
              </label>
              <Input
                type="number"
                min="0"
                step="0.01"
                value={settingsForm.pickup_fee}
                onChange={e => setSettingsForm(f => ({ ...f, pickup_fee: e.target.value }))}
              />
            </div>

            {settingsError && (
              <p className="text-red-500 text-xs mb-3">{settingsError}</p>
            )}

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setEditingSettings(false)} disabled={savingSettings}>
                Cancel
              </Button>
              <Button onClick={saveSettings} disabled={savingSettings}>
                {savingSettings ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
