'use client';

import { useEffect, useState } from 'react';
import { addressesApi, ApiError } from '@/lib/api';
import { Address, AddressCreate, EmirateEnum } from '@/lib/types';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui';

const EMIRATES: { value: string; label: string }[] = [
  { value: 'Dubai', label: 'Dubai' },
  { value: 'Sharjah', label: 'Sharjah' },
  { value: 'Ajman', label: 'Ajman' },
  { value: 'Abu Dhabi', label: 'Abu Dhabi' },
  { value: 'Ras Al Khaimah', label: 'Ras Al Khaimah' },
  { value: 'Fujairah', label: 'Fujairah' },
  { value: 'Umm Al Quwain', label: 'Umm Al Quwain' },
];

const BLANK_FORM: AddressCreate = {
  label: 'Home',
  first_name: '',
  last_name: '',
  phone: '',
  address_line_1: '',
  address_line_2: '',
  city: '',
  emirate: 'Dubai',
  country: 'AE',
  is_default: false,
};

export default function AddressesPage() {
  const { addToast } = useToast();
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<AddressCreate>(BLANK_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof AddressCreate, string>>>({});
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

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
      city: addr.city,
      emirate: addr.emirate,
      country: addr.country,
      is_default: addr.is_default,
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
    if (!form.first_name.trim()) e.first_name = 'Required';
    if (!form.last_name.trim()) e.last_name = 'Required';
    if (!form.phone.trim()) e.phone = 'Required';
    if (!form.address_line_1.trim()) e.address_line_1 = 'Required';
    if (!form.city.trim()) e.city = 'Required';
    if (!form.emirate) e.emirate = 'Required';
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
        <h1 className="font-display text-2xl text-primary mb-6">Addresses</h1>
        <div className="space-y-3">
          {[1, 2].map(i => <div key={i} className="h-28 bg-gray-100 animate-pulse rounded-sm" />)}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-display text-2xl text-primary">Addresses</h1>
        {!showForm && (
          <Button variant="ghost" size="sm" onClick={openAdd}>
            <span className="material-icons text-[16px]">add</span>
            Add Address
          </Button>
        )}
      </div>

      {showForm && (
        <div className="mb-8 border border-primary/30 p-5 bg-primary/5">
          <h2 className="text-xs font-body uppercase tracking-widest text-primary mb-4">
            {editId ? 'Edit Address' : 'New Address'}
          </h2>
          <div className="space-y-3">
            <Input
              label="Label (e.g. Home, Work)"
              value={form.label}
              onChange={e => setForm(f => ({ ...f, label: e.target.value }))}
            />
            <div className="grid grid-cols-2 gap-3">
              <Input
                label="First Name"
                value={form.first_name}
                onChange={e => setForm(f => ({ ...f, first_name: e.target.value }))}
                error={errors.first_name}
              />
              <Input
                label="Last Name"
                value={form.last_name}
                onChange={e => setForm(f => ({ ...f, last_name: e.target.value }))}
                error={errors.last_name}
              />
            </div>
            <Input
              label="Phone"
              type="tel"
              value={form.phone}
              onChange={e => setForm(f => ({ ...f, phone: e.target.value }))}
              error={errors.phone}
              placeholder="+971 50 000 0000"
            />
            <Input
              label="Address Line 1"
              value={form.address_line_1}
              onChange={e => setForm(f => ({ ...f, address_line_1: e.target.value }))}
              error={errors.address_line_1}
            />
            <Input
              label="Address Line 2 (optional)"
              value={form.address_line_2}
              onChange={e => setForm(f => ({ ...f, address_line_2: e.target.value }))}
            />
            <div className="grid grid-cols-2 gap-3">
              <Input
                label="City"
                value={form.city}
                onChange={e => setForm(f => ({ ...f, city: e.target.value }))}
                error={errors.city}
              />
              <Select
                label="Emirate"
                value={form.emirate}
                onChange={e => setForm(f => ({ ...f, emirate: e.target.value as EmirateEnum }))}
                options={EMIRATES}
                error={errors.emirate}
              />
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.is_default}
                onChange={e => setForm(f => ({ ...f, is_default: e.target.checked }))}
                className="accent-primary"
              />
              <span className="text-xs text-gray-600 font-body uppercase tracking-widest">Set as default address</span>
            </label>
          </div>
          <div className="flex gap-3 mt-5">
            <Button onClick={handleSave} loading={saving}>
              {editId ? 'Save Changes' : 'Add Address'}
            </Button>
            <Button variant="ghost" onClick={closeForm} disabled={saving}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {addresses.length === 0 && !showForm ? (
        <div className="text-center py-12 border border-dashed border-gray-200">
          <span className="material-icons text-4xl text-gray-300 block mb-3">location_off</span>
          <p className="text-sm text-gray-500 font-body mb-4">No saved addresses yet.</p>
          <Button variant="ghost" size="sm" onClick={openAdd}>Add Your First Address</Button>
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
                        Default
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
                  <p className="text-sm text-gray-600 font-body">{addr.city}, {addr.emirate}</p>
                  <p className="text-xs text-gray-400 font-body mt-1">{addr.phone}</p>
                </div>
                <div className="flex flex-col gap-1.5 shrink-0">
                  <button
                    onClick={() => openEdit(addr)}
                    className="text-xs text-gray-500 hover:text-primary font-body uppercase tracking-wide transition-colors"
                  >
                    Edit
                  </button>
                  {!addr.is_default && (
                    <button
                      onClick={() => handleSetDefault(addr.id)}
                      className="text-xs text-gray-500 hover:text-primary font-body uppercase tracking-wide transition-colors"
                    >
                      Set Default
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(addr.id)}
                    disabled={deletingId === addr.id}
                    className="text-xs text-gray-400 hover:text-red-500 font-body uppercase tracking-wide transition-colors disabled:opacity-50"
                  >
                    {deletingId === addr.id ? 'Removing...' : 'Remove'}
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
