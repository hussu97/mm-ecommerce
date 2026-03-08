'use client';

import { useEffect, useState } from 'react';
import { addressesApi, ApiError } from '@/lib/api';
import { Address, AddressCreate, RegionCode } from '@/lib/types';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { LocationPicker } from '@/components/ui/LocationPicker';

const REGION_CODES: RegionCode[] = [
  'dubai', 'sharjah', 'ajman', 'abu_dhabi',
  'fujairah', 'ras_al_khaimah', 'umm_al_quwain', 'al_ain', 'rest_of_uae',
];

const BLANK_FORM: AddressCreate = {
  label: 'Home',
  first_name: '',
  last_name: '',
  phone: '',
  address_line_1: '',
  address_line_2: '',
  region: 'dubai',
  country: 'AE',
  is_default: false,
  latitude: null,
  longitude: null,
};

export default function AddressesPage() {
  const { addToast } = useToast();
  const { t } = useTranslation();
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<AddressCreate>(BLANK_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof AddressCreate, string>>>({});
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const REGION_OPTIONS = REGION_CODES.map((code) => ({
    value: code,
    label: t(`regions.${code}`),
  }));

  useEffect(() => {
    addressesApi.list()
      .then(setAddresses)
      .finally(() => setLoading(false));
  }, []);

  function openAdd() {
    setEditId(null);
    setForm(BLANK_FORM);
    setErrors({});
    setShowForm(true);
  }

  function openEdit(addr: Address) {
    setEditId(addr.id);
    setForm({
      label: addr.label,
      first_name: addr.first_name,
      last_name: addr.last_name,
      phone: addr.phone,
      address_line_1: addr.address_line_1,
      address_line_2: addr.address_line_2 || '',
      region: addr.region,
      country: addr.country,
      is_default: addr.is_default,
      latitude: addr.latitude,
      longitude: addr.longitude,
    });
    setErrors({});
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditId(null);
  }

  function validate() {
    const e: Partial<Record<keyof AddressCreate, string>> = {};
    if (!form.first_name.trim()) e.first_name = t('common.required');
    if (!form.last_name.trim()) e.last_name = t('common.required');
    if (!form.phone.trim()) e.phone = t('common.required');
    if (!form.address_line_1.trim()) e.address_line_1 = t('common.required');
    if (!form.region) e.region = t('common.required');
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSave() {
    if (!validate()) return;
    setSaving(true);
    try {
      const data = { ...form, address_line_2: form.address_line_2 || undefined };
      if (editId) {
        const updated = await addressesApi.update(editId, data);
        setAddresses(prev => prev.map(a => a.id === editId ? updated : a));
        addToast('Address updated', 'success');
      } else {
        const created = await addressesApi.create(data);
        setAddresses(prev => [...prev, created]);
        addToast('Address added', 'success');
      }
      closeForm();
    } catch (err) {
      addToast(err instanceof ApiError ? err.message : 'Failed to save address', 'error');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await addressesApi.delete(id);
      setAddresses(prev => prev.filter(a => a.id !== id));
      addToast('Address removed', 'success');
    } catch {
      addToast('Failed to delete address', 'error');
    } finally {
      setDeletingId(null);
    }
  }

  async function handleSetDefault(id: string) {
    try {
      const updated = await addressesApi.setDefault(id);
      setAddresses(prev => prev.map(a => ({ ...a, is_default: a.id === id ? updated.is_default : false })));
    } catch {
      addToast('Failed to update default', 'error');
    }
  }

  if (loading) {
    return (
      <div>
        <h1 className="font-display text-2xl text-primary mb-6">{t('address.title')}</h1>
        <div className="space-y-3">
          {[1, 2].map(i => <div key={i} className="h-28 bg-gray-100 animate-pulse rounded-sm" />)}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-display text-2xl text-primary">{t('address.title')}</h1>
        {!showForm && (
          <Button variant="ghost" size="sm" onClick={openAdd}>
            <span className="material-icons text-[16px]">add</span>
            {t('address.add_address')}
          </Button>
        )}
      </div>

      {showForm && (
        <div className="mb-8 border border-primary/30 p-5 bg-primary/5">
          <h2 className="text-xs font-body uppercase tracking-widest text-primary mb-4">
            {editId ? t('address.edit_address') : t('address.new_address')}
          </h2>
          <div className="space-y-3">
            <Input
              label={t('address.label_hint')}
              value={form.label}
              onChange={e => setForm(f => ({ ...f, label: e.target.value }))}
            />
            <div className="grid grid-cols-2 gap-3">
              <Input
                label={t('common.first_name')}
                value={form.first_name}
                onChange={e => setForm(f => ({ ...f, first_name: e.target.value }))}
                error={errors.first_name}
              />
              <Input
                label={t('common.last_name')}
                value={form.last_name}
                onChange={e => setForm(f => ({ ...f, last_name: e.target.value }))}
                error={errors.last_name}
              />
            </div>
            <Input
              label={t('common.phone')}
              type="tel"
              value={form.phone}
              onChange={e => setForm(f => ({ ...f, phone: e.target.value }))}
              error={errors.phone}
              placeholder={t('common.phone_placeholder')}
            />
            <Input
              label={t('common.address_line_1')}
              value={form.address_line_1}
              onChange={e => setForm(f => ({ ...f, address_line_1: e.target.value }))}
              error={errors.address_line_1}
            />
            <Input
              label={t('common.address_line_2_optional')}
              value={form.address_line_2}
              onChange={e => setForm(f => ({ ...f, address_line_2: e.target.value }))}
            />
            <Select
              label={t('address.region')}
              value={form.region}
              onChange={e => setForm(f => ({ ...f, region: e.target.value as RegionCode }))}
              options={REGION_OPTIONS}
              error={errors.region}
            />
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
                {t('address.pin_location')}
              </p>
              <LocationPicker
                lat={form.latitude ?? null}
                lng={form.longitude ?? null}
                onChange={(lat, lng) => setForm(f => ({ ...f, latitude: lat, longitude: lng }))}
              />
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.is_default}
                onChange={e => setForm(f => ({ ...f, is_default: e.target.checked }))}
                className="accent-primary"
              />
              <span className="text-xs text-gray-600 font-body uppercase tracking-widest">{t('address.set_as_default')}</span>
            </label>
          </div>
          <div className="flex gap-3 mt-5">
            <Button onClick={handleSave} loading={saving}>
              {editId ? t('common.save_changes') : t('address.add_address')}
            </Button>
            <Button variant="ghost" onClick={closeForm} disabled={saving}>
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}

      {addresses.length === 0 && !showForm ? (
        <div className="text-center py-12 border border-dashed border-gray-200">
          <span className="material-icons text-4xl text-gray-300 block mb-3">location_off</span>
          <p className="text-sm text-gray-500 font-body mb-4">{t('address.no_addresses')}</p>
          <Button variant="ghost" size="sm" onClick={openAdd}>{t('address.add_first')}</Button>
        </div>
      ) : (
        <div className="space-y-3">
          {addresses.map(addr => (
            <div
              key={addr.id}
              className={`border p-4 ${addr.is_default ? 'border-primary bg-primary/5' : 'border-gray-200'}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-medium uppercase tracking-widest text-gray-700 font-body">
                      {addr.label}
                    </span>
                    {addr.is_default && (
                      <span className="text-[10px] bg-primary text-white px-1.5 py-0.5 uppercase tracking-wide font-body">
                        {t('address.default_badge')}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-gray-700 font-body">
                    {addr.first_name} {addr.last_name}
                  </p>
                  <p className="text-sm text-gray-600 font-body">{addr.address_line_1}</p>
                  {addr.address_line_2 && (
                    <p className="text-sm text-gray-600 font-body">{addr.address_line_2}</p>
                  )}
                  <p className="text-sm text-gray-600 font-body">{t(`regions.${addr.region}`)}</p>
                  <p className="text-xs text-gray-400 font-body mt-1">{addr.phone}</p>
                </div>
                <div className="flex flex-col gap-1.5 shrink-0">
                  <button
                    onClick={() => openEdit(addr)}
                    className="text-xs text-gray-500 hover:text-primary font-body uppercase tracking-wide transition-colors"
                  >
                    {t('common.edit')}
                  </button>
                  {!addr.is_default && (
                    <button
                      onClick={() => handleSetDefault(addr.id)}
                      className="text-xs text-gray-500 hover:text-primary font-body uppercase tracking-wide transition-colors"
                    >
                      {t('address.set_default')}
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(addr.id)}
                    disabled={deletingId === addr.id}
                    className="text-xs text-gray-400 hover:text-red-500 font-body uppercase tracking-wide transition-colors disabled:opacity-50"
                  >
                    {deletingId === addr.id ? t('address.removing') : t('common.remove')}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
