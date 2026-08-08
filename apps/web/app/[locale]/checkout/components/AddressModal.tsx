'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Spinner';
import { PhoneInput, isValidPhone } from '@/components/ui/PhoneInput';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { addressesApi } from '@/lib/api';
import { guestAddresses } from '@/lib/guest-addresses';
import { reverseGeocode } from '@/lib/geocode';
import type { Address } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

const LocationPicker = dynamic(
  () => import('@/components/ui/LocationPicker').then((m) => ({ default: m.LocationPicker })),
  { ssr: false, loading: () => <div className="h-72 bg-gray-100 rounded-sm animate-pulse" /> },
);

export interface AddressDraft {
  id: string;
  label: string;
  firstName: string;
  lastName: string;
  phone: string;
  addressLine1: string;
  addressLine2: string;
  unitNumber: string;
  latitude: number | null;
  longitude: number | null;
}

const EMPTY: AddressDraft = {
  id: '', label: 'Home', firstName: '', lastName: '', phone: '',
  addressLine1: '', addressLine2: '', unitNumber: '',
  latitude: null, longitude: null,
};

export function toDraft(a: Address): AddressDraft {
  return {
    id: a.id,
    label: a.label,
    firstName: a.first_name,
    lastName: a.last_name,
    phone: a.phone,
    addressLine1: a.address_line_1,
    addressLine2: a.address_line_2 ?? '',
    unitNumber: a.unit_number ?? '',
    latitude: a.latitude === null ? null : Number(a.latitude),
    longitude: a.longitude === null ? null : Number(a.longitude),
  };
}

/** One line a courier can read, used on the collapsed card and the list rows. */
export function formatAddress(a: AddressDraft): string {
  return [a.unitNumber, a.addressLine1]
    .map((p) => p?.trim())
    .filter(Boolean)
    .join(', ');
}

interface AddressModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (draft: AddressDraft) => void;
  isAuthenticated: boolean;
  /** Addresses already on file, so the modal can offer them before asking again. */
  savedAddresses: Address[];
  onSavedAddressesChange: (list: Address[]) => void;
  selectedAddressId: string;
  /** Opens straight on the form when the customer has nothing saved yet. */
  initialDraft?: AddressDraft | null;
}

export function AddressModal({
  isOpen, onClose, onSave, isAuthenticated,
  savedAddresses, onSavedAddressesChange, selectedAddressId, initialDraft,
}: AddressModalProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'list' | 'form'>('list');
  const [draft, setDraft] = useState<AddressDraft>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [geocoding, setGeocoding] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Reopening should not resume a half-finished edit from last time.
  useEffect(() => {
    if (!isOpen) return;
    setErrors({});
    if (savedAddresses.length > 0) {
      setMode('list');
    } else {
      setDraft(initialDraft ?? EMPTY);
      setMode('form');
    }
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll lock + Escape, matching the shared Modal's behaviour.
  useEffect(() => {
    if (!isOpen) return;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = '';
      window.removeEventListener('keydown', onKey);
    };
  }, [isOpen, onClose]);

  /**
   * Moving the pin rewrites the address, every time.
   *
   * It used to fill the field only when it was empty, so the second pin drop
   * did nothing visible: the customer corrected their location and the address
   * underneath still described the first guess. The pin is the statement of
   * where they are, so it wins — and the field stays editable underneath.
   */
  const handlePin = useCallback(async (lat: number, lng: number, selectedAddress?: string) => {
    setDraft((prev) => ({ ...prev, latitude: lat, longitude: lng }));
    if (selectedAddress) {
      setDraft((prev) => ({ ...prev, addressLine1: selectedAddress }));
      setErrors((prev) => {
        const next = { ...prev };
        delete next.addressLine1;
        return next;
      });
      return;
    }
    setGeocoding(true);
    const found = await reverseGeocode(lat, lng);
    setGeocoding(false);
    if (!found) return;
    setDraft((prev) => ({
      ...prev,
      addressLine1: found.address,
    }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next.addressLine1;
      return next;
    });
  }, []);

  const field = (key: keyof AddressDraft) => (value: string) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => { const next = { ...prev }; delete next[key]; return next; });
  };

  function validate(d: AddressDraft): Record<string, string> {
    const e: Record<string, string> = {};
    if (!d.firstName.trim()) e.firstName = t('checkout.first_name_required');
    if (!d.lastName.trim()) e.lastName = t('checkout.last_name_required');
    if (!d.phone.trim() || !isValidPhone(d.phone)) e.phone = t('checkout.valid_phone_required');
    if (!d.addressLine1.trim()) e.addressLine1 = t('checkout.address_required');
    return e;
  }

  const handleSubmit = async () => {
    const found = validate(draft);
    if (Object.keys(found).length > 0) {
      setErrors(found);
      requestAnimationFrame(() => {
        bodyRef.current
          ?.querySelector<HTMLElement>('[data-field-error="true"]')
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      return;
    }

    setSaving(true);
    const payload = {
      label: draft.label || 'Home',
      first_name: draft.firstName.trim(),
      last_name: draft.lastName.trim(),
      phone: draft.phone.trim(),
      address_line_1: draft.addressLine1.trim(),
      address_line_2: draft.addressLine2.trim() || undefined,
      unit_number: draft.unitNumber.trim() || undefined,
      latitude: draft.latitude,
      longitude: draft.longitude,
    };

    try {
      if (isAuthenticated) {
        const saved = draft.id
          ? await addressesApi.update(draft.id, payload)
          : await addressesApi.create(payload);
        const list = await addressesApi.list();
        onSavedAddressesChange(list);
        onSave(toDraft(saved));
      } else {
        const saved = draft.id
          ? guestAddresses.update(draft.id, payload)
          : guestAddresses.create(payload);
        onSavedAddressesChange(guestAddresses.list());
        onSave(toDraft(saved ?? guestAddresses.create(payload)));
      }
      onClose();
    } catch {
      // Saving to the address book is a convenience; never let it block the
      // order. Use the details as typed and move on.
      onSave({ ...draft, id: draft.id || 'unsaved' });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (isAuthenticated) {
      try {
        await addressesApi.delete(id);
        onSavedAddressesChange(await addressesApi.list());
      } catch { /* leave the list as-is; the row simply stays */ }
    } else {
      guestAddresses.remove(id);
      onSavedAddressesChange(guestAddresses.list());
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />

      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('checkout.delivery_address')}
        className="relative z-10 w-full sm:max-w-lg bg-white sm:rounded-sm shadow-xl h-[92vh] sm:h-auto sm:max-h-[90vh] flex flex-col"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            {mode === 'form' && savedAddresses.length > 0 && (
              <button
                onClick={() => setMode('list')}
                className="p-1 -ms-1 text-gray-400 hover:text-primary transition-colors"
                aria-label={t('common.back')}
              >
                <Icon name="arrow_back" className="text-xl" />
              </button>
            )}
            <h3 className="font-display text-lg text-primary tracking-wide truncate">
              {mode === 'list'
                ? t('checkout.delivery_address')
                : draft.id ? t('address.edit_address') : t('address.new_address')}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
            aria-label={t('common.close')}
          >
            <Icon name="close" className="text-xl" />
          </button>
        </div>

        <div ref={bodyRef} className="flex-1 overflow-y-auto px-5 py-5">
          {mode === 'list' ? (
            <div className="space-y-3">
              {savedAddresses.map((a) => {
                const d = toDraft(a);
                const active = a.id === selectedAddressId;
                return (
                  <div
                    key={a.id}
                    className={`border rounded-sm p-3 transition-colors ${
                      active ? 'border-primary bg-primary/5' : 'border-gray-200'
                    }`}
                  >
                    <button
                      onClick={() => { onSave(d); onClose(); }}
                      className="w-full text-start"
                    >
                      <p className="font-body text-sm font-medium text-gray-800">
                        {a.label}
                        {a.is_default && (
                          <span className="ms-2 text-[10px] uppercase tracking-wider text-secondary">
                            {t('address.default')}
                          </span>
                        )}
                      </p>
                      <p className="font-body text-xs text-gray-500 mt-0.5">
                        {formatAddress(d)}
                      </p>
                      <p className="font-body text-xs text-gray-400 mt-0.5">
                        {d.firstName} {d.lastName} · {d.phone}
                      </p>
                    </button>
                    <div className="flex gap-4 mt-2 pt-2 border-t border-gray-100">
                      <button
                        onClick={() => { setDraft(d); setMode('form'); }}
                        className="text-xs font-body text-primary hover:underline"
                      >
                        {t('common.edit')}
                      </button>
                      <button
                        onClick={() => handleDelete(a.id)}
                        className="text-xs font-body text-gray-400 hover:text-red-500 transition-colors"
                      >
                        {t('common.delete')}
                      </button>
                    </div>
                  </div>
                );
              })}

              <button
                onClick={() => { setDraft(EMPTY); setMode('form'); }}
                className="w-full flex items-center justify-center gap-2 py-3 border border-dashed border-gray-300 rounded-sm text-sm font-body text-primary hover:border-primary transition-colors"
              >
                <Icon name="add" className="text-base" />
                {t('address.new_address')}
              </button>
            </div>
          ) : (
            <div className="space-y-5">
              {/* The map leads: it is the fastest way to answer most of what
                  follows, and on a phone it is far easier than typing. */}
              <div>
                <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
                  {t('address.pin_location')}
                </p>
                <LocationPicker
                  lat={draft.latitude}
                  lng={draft.longitude}
                  onChange={handlePin}
                  placeholder={t('address.search_location')}
                  height="280px"
                />
                {geocoding && (
                  <p className="mt-2 flex items-center gap-2 text-xs text-gray-400 font-body">
                    <Spinner size="sm" /> {t('address.finding_address')}
                  </p>
                )}
              </div>

              <div className="h-px bg-secondary/30" />

              <div className="space-y-4">
                <div data-field-error={errors.addressLine1 ? 'true' : undefined}>
                  <Input
                    label={t('common.address')}
                    placeholder={t('checkout.address_placeholder')}
                    value={draft.addressLine1}
                    onChange={(e) => field('addressLine1')(e.target.value)}
                    error={errors.addressLine1}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <Input
                    label={t('address.unit_number')}
                    placeholder={t('address.unit_placeholder')}
                    value={draft.unitNumber}
                    onChange={(e) => field('unitNumber')(e.target.value)}
                  />
                  <Input
                    label={t('address.label')}
                    placeholder={t('address.label_placeholder')}
                    value={draft.label}
                    onChange={(e) => field('label')(e.target.value)}
                  />
                </div>

              </div>

              <div className="h-px bg-secondary/30" />

              {/* Who receives it and how the rider reaches them — part of the
                  address, not a separate form somewhere above. */}
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div data-field-error={errors.firstName ? 'true' : undefined}>
                    <Input
                      label={t('common.first_name')}
                      placeholder={t('checkout.first_name_placeholder')}
                      value={draft.firstName}
                      onChange={(e) => field('firstName')(e.target.value)}
                      error={errors.firstName}
                    />
                  </div>
                  <div data-field-error={errors.lastName ? 'true' : undefined}>
                    <Input
                      label={t('common.last_name')}
                      placeholder={t('checkout.last_name_placeholder')}
                      value={draft.lastName}
                      onChange={(e) => field('lastName')(e.target.value)}
                      error={errors.lastName}
                    />
                  </div>
                </div>
                <div data-field-error={errors.phone ? 'true' : undefined}>
                  <PhoneInput
                    label={t('common.phone')}
                    value={draft.phone}
                    onChange={field('phone')}
                    error={errors.phone}
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        {mode === 'form' && (
          <div className="px-5 py-4 border-t border-gray-100 shrink-0">
            <Button variant="primary" size="lg" fullWidth onClick={handleSubmit} loading={saving}>
              {t('address.save_and_continue')}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
