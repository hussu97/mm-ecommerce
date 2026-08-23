'use client';

import { useEffect, useState } from 'react';
import { analytics, failureReason } from '@/lib/analytics';
import { addressesApi, authApi, ApiError } from '@/lib/api';
import { DEFAULT_ADDRESS_LABEL } from '@/lib/guest-addresses';
import { Address, AddressCreate, AddressFormDraft } from '@/lib/types';
import { Input } from '@/components/ui/Input';
import { PhoneInput, isValidPhone } from '@/components/ui/PhoneInput';
import { PhoneVerify } from '@/components/ui/PhoneVerify';
import { useLocation } from '@/lib/location/LocationProvider';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import dynamic from 'next/dynamic';
import { Icon } from '@/components/ui/Icon';

/**
 * The map, loaded when the form that needs it is open.
 *
 * `@vis.gl/react-google-maps` plus the Maps JS API is the heaviest thing this
 * route can pull in, and it is only reachable behind "add" or "edit". Checkout
 * already did this in `AddressModal`; this page was the one place still
 * importing it statically, so opening the address book downloaded a map nobody
 * had asked for yet.
 */
const LocationPicker = dynamic(
  () => import('@/components/ui/LocationPicker').then((m) => m.LocationPicker),
  { ssr: false },
);

/**
 * The same address, asked for the same way as at checkout.
 *
 * This form had drifted: it offered an Address Line 2 the checkout does not
 * have, omitted the flat/office/floor the driver actually needs, took the phone
 * as free text so a pasted `+971 50 …` had nowhere to go, and left every field
 * unlabelled by example. Two forms for one record is two chances to teach a
 * customer a different shape of the same thing, so this one now mirrors the
 * checkout: the map leads, then the address, then who receives it.
 */
const BLANK_FORM: AddressFormDraft = {
  label: DEFAULT_ADDRESS_LABEL,
  first_name: '',
  last_name: '',
  phone: '',
  address_line_1: '',
  unit_number: '',
  country: 'AE',
  is_default: false,
  latitude: null,
  longitude: null,
};

export default function AddressesPage() {
  const { addToast } = useToast();
  const { t } = useTranslation();
  // Changing where you live has to move every delivery estimate on the site.
  const { refreshFromAddresses } = useLocation();
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<AddressFormDraft>(BLANK_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof AddressFormDraft, string>>>({});
  // The number most recently proved, so the tick disappears the moment the
  // field is edited to something else.
  const [verifiedPhone, setVerifiedPhone] = useState<string | null>(null);
  // See AddressModal: locks the field while a code is outstanding.
  const [codePending, setCodePending] = useState(false);
  // Ask the server whether this number is *already* proved before offering to
  // prove it. A verification belongs to the handset, not to the address it was
  // first typed on — somebody adding a second address should not sit through a
  // second SMS for the same number.
  useEffect(() => {
    const phone = form.phone;
    if (!phone || !isValidPhone(phone) || verifiedPhone === phone) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      authApi
        .phoneVerified(phone)
        .then(r => { if (!cancelled && r.verified) setVerifiedPhone(phone); })
        .catch(() => { /* offer the button; a failed check must not block saving */ });
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [form.phone, verifiedPhone]);
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
      unit_number: addr.unit_number || '',
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
    const e: Partial<Record<keyof AddressFormDraft, string>> = {};
    if (!form.first_name.trim()) e.first_name = t('checkout.first_name_required');
    if (!form.last_name.trim()) e.last_name = t('checkout.last_name_required');
    // Same bar as checkout: a number that is merely non-empty still strands a
    // driver outside a building.
    if (!form.phone.trim() || !isValidPhone(form.phone)) {
      e.phone = t('checkout.valid_phone_required');
    }
    if (!form.address_line_1.trim()) e.address_line_1 = t('checkout.address_required');
    // The API requires the pin and delivery zones are priced off it. Without
    // this the form posted `latitude: null` and took a 422 back.
    if (form.latitude === null || form.longitude === null) {
      e.latitude = t('checkout.address_pin_required');
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSave() {
    if (!validate()) return;
    setSaving(true);
    try {
      const data: AddressCreate = {
        ...form,
        unit_number: form.unit_number || undefined,
        latitude: form.latitude as number,
        longitude: form.longitude as number,
      };
      if (editId) {
        const updated = await addressesApi.update(editId, data);
      void refreshFromAddresses();
        setAddresses(prev => prev.map(a => a.id === editId ? updated : a));
        addToast('Address updated', 'success');
      } else {
        const created = await addressesApi.create(data);
      void refreshFromAddresses();
        setAddresses(prev => [...prev, created]);
        addToast('Address added', 'success');
      }
      analytics.addressSaved({
        surface: 'account',
        has_pin: form.latitude !== null && form.longitude !== null,
        is_new: !editId,
      });
      closeForm();
    } catch (err) {
      analytics.addressSaveFailed({ surface: 'account', reason: failureReason(err) });
      addToast(err instanceof ApiError ? err.message : 'Failed to save address', 'error');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await addressesApi.delete(id);
      void refreshFromAddresses();
      setAddresses(prev => prev.filter(a => a.id !== id));
      analytics.addressDeleted({ surface: 'account' });
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
      void refreshFromAddresses();
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
            <Icon name="add" className="text-[16px]" />
            {t('address.add_address')}
          </Button>
        )}
      </div>

      {showForm && (
        <div className="mb-8 border border-primary/30 p-5 bg-primary/5">
          <h2 className="text-xs font-body uppercase tracking-widest text-primary mb-4">
            {editId ? t('address.edit_address') : t('address.new_address')}
          </h2>
          <div className="space-y-5">
            {/* The map leads, exactly as it does at checkout: it is the fastest
                way to answer most of what follows. */}
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
                {t('address.pin_location')}
              </p>
              <LocationPicker
                lat={form.latitude ?? null}
                lng={form.longitude ?? null}
                onChange={(lat, lng, selectedAddress) => setForm(f => ({
                  ...f,
                  latitude: lat,
                  longitude: lng,
                  address_line_1: selectedAddress ?? f.address_line_1,
                }))}
                placeholder={t('address.search_location')}
              />
              {errors.latitude && (
                <p className="mt-1.5 text-xs text-red-500">{errors.latitude}</p>
              )}
            </div>

            <div className="h-px bg-secondary/30" />

            <div className="space-y-4">
              <Input
                label={t('common.address')}
                placeholder={t('checkout.address_placeholder')}
                value={form.address_line_1}
                onChange={e => setForm(f => ({ ...f, address_line_1: e.target.value }))}
                error={errors.address_line_1}
              />
              <div className="grid grid-cols-2 gap-4">
                {/* The unit number is the part that finishes the job — a
                    formatted address gets a driver to the building. */}
                <Input
                  label={t('address.unit_number')}
                  placeholder={t('address.unit_placeholder')}
                  value={form.unit_number ?? ''}
                  onChange={e => setForm(f => ({ ...f, unit_number: e.target.value }))}
                />
                <Input
                  label={t('address.label')}
                  placeholder={t('address.label_placeholder')}
                  value={form.label ?? ''}
                  onChange={e => setForm(f => ({ ...f, label: e.target.value }))}
                />
              </div>
            </div>

            <div className="h-px bg-secondary/30" />

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Input
                  label={t('common.first_name')}
                  placeholder={t('checkout.first_name_placeholder')}
                  value={form.first_name}
                  onChange={e => setForm(f => ({ ...f, first_name: e.target.value }))}
                  error={errors.first_name}
                />
                <Input
                  label={t('common.last_name')}
                  placeholder={t('checkout.last_name_placeholder')}
                  value={form.last_name}
                  onChange={e => setForm(f => ({ ...f, last_name: e.target.value }))}
                  error={errors.last_name}
                />
              </div>
              {/* The same control as checkout, so a number pasted out of a
                  contact card with its own country code still lands. */}
              <PhoneInput
                label={t('common.phone')}
                value={form.phone}
                onChange={v => {
                  // Editing the number invalidates any proof already held for
                  // it. Keeping the tick beside a changed number would show a
                  // verification of something else.
                  setVerifiedPhone(p => (p === v ? p : null));
                  setForm(f => ({ ...f, phone: v }));
                }}
                error={errors.phone}
                // See AddressModal: the number is fixed while a code is out.
                disabled={codePending}
              />
              {/* Optional here, and deliberately so: saving an address should
                  not require an SMS. It is what unlocks the new-customer
                  offer, and the offer is the reason to bother. */}
              {isValidPhone(form.phone) && verifiedPhone !== form.phone && (
                <PhoneVerify surface="account"
                  phone={form.phone}
                  onVerified={setVerifiedPhone}
                  onCodePending={setCodePending}
                  className="mt-2"
                />
              )}
              {verifiedPhone === form.phone && form.phone && (
                <p className="mt-2 flex items-center gap-1.5 text-sm text-green-700">
                  <Icon name="check_circle" className="text-[18px]" />
                  {t('verify.verified')}
                </p>
              )}
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
          <Icon name="location_off" className="text-4xl text-gray-300 block mb-3" />
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
                {/* Masked out of Clarity recordings, with its children: this is
                    a customer's name, home address and phone rendered as plain
                    text, which Clarity's default mode does not treat as
                    sensitive — only input boxes and dropdowns are masked
                    everywhere. The label and the buttons stay outside it, so a
                    recording still shows which card was acted on. */}
                <div className="min-w-0" data-clarity-mask="true">
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
                  {/* Unit first, then the street — the same one line the
                      checkout shows, because a formatted address gets a driver
                      to the building and the flat number finishes the job. */}
                  <p className="text-sm text-gray-600 font-body">
                    {[addr.unit_number, addr.address_line_1].filter(Boolean).join(', ')}
                  </p>
                  {/* Only ever present on addresses saved before this form
                      stopped asking for it. Shown rather than silently dropped:
                      somebody typed it because it mattered. */}
                  {addr.address_line_2 && (
                    <p className="text-sm text-gray-600 font-body">{addr.address_line_2}</p>
                  )}
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
