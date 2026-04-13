'use client';

import { useState } from 'react';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import dynamic from 'next/dynamic';
import type { Address, PublicRegion } from '@/lib/types';

const LocationPicker = dynamic(
  () => import('@/components/ui/LocationPicker').then(m => ({ default: m.LocationPicker })),
  { ssr: false, loading: () => <div className="h-64 bg-gray-100 rounded-lg animate-pulse" /> },
);

export interface AddressFormValues {
  selectedAddressId: string;
  firstName: string;
  lastName: string;
  phone: string;
  addressLine1: string;
  addressLine2: string;
  region: string;
  locationLat: number | null;
  locationLng: number | null;
}

interface AddressFormProps {
  values: AddressFormValues;
  onChange: (patch: Partial<AddressFormValues>) => void;
  errors: Record<string, string>;
  onClearError: (key: string) => void;
  savedAddresses: Address[];
  loadingAddresses: boolean;
  regions: PublicRegion[];
}

export function AddressForm({
  values, onChange, errors, onClearError, savedAddresses, loadingAddresses, regions,
}: AddressFormProps) {
  const { t, locale } = useTranslation();

  const REGION_OPTIONS = regions.map((r) => ({
    value: r.slug,
    label: r.name_translations[locale] ?? r.name_translations['en'] ?? r.slug,
  }));

  const field = (key: keyof AddressFormValues) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      onChange({ [key]: e.target.value });
      onClearError(key);
    };

  return (
    <div>
      <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">
        {t('checkout.delivery_address')}
      </h2>
      <p className="font-body text-xs text-gray-400 mb-4">{t('checkout.address_hint')}</p>
      <div className="h-px bg-secondary/30 mb-5" />

      {/* Saved addresses for authenticated users */}
      {loadingAddresses ? (
        <div className="flex items-center gap-2 text-gray-400 mb-4">
          <Spinner size="sm" />
          <span className="font-body text-xs">{t('checkout.loading_addresses')}</span>
        </div>
      ) : savedAddresses.length > 0 ? (
        <div className="space-y-2 mb-5">
          {savedAddresses.map((addr) => (
            <label
              key={addr.id}
              className="flex gap-3 items-start p-3 border rounded-sm cursor-pointer hover:border-primary/50 transition-colors"
            >
              <input
                type="radio"
                name="savedAddress"
                value={addr.id}
                checked={values.selectedAddressId === addr.id}
                onChange={() => {
                  onChange({
                    selectedAddressId: addr.id,
                    firstName: addr.first_name,
                    lastName: addr.last_name,
                    phone: addr.phone,
                    addressLine1: addr.address_line_1,
                    addressLine2: addr.address_line_2 ?? '',
                    region: addr.region,
                    locationLat: addr.latitude ?? null,
                    locationLng: addr.longitude ?? null,
                  });
                  onClearError('addressLine1');
                  onClearError('region');
                }}
                className="mt-0.5 accent-primary"
              />
              <div className="font-body text-xs">
                <p className="font-medium text-gray-800">{addr.label}</p>
                <p className="text-gray-500">
                  {addr.address_line_1}, {t(`regions.${addr.region}`)}
                </p>
              </div>
            </label>
          ))}
          <label className="flex gap-3 items-start p-3 border rounded-sm cursor-pointer hover:border-primary/50 transition-colors">
            <input
              type="radio"
              name="savedAddress"
              value=""
              checked={values.selectedAddressId === ''}
              onChange={() => onChange({ selectedAddressId: '', locationLat: null, locationLng: null })}
              className="mt-0.5 accent-primary"
            />
            <span className="font-body text-xs text-gray-600">
              {t('checkout.new_address_option')}
            </span>
          </label>
        </div>
      ) : null}

      {/* New address form */}
      {values.selectedAddressId === '' && (
        <div className="space-y-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
              {t('address.pin_location')}
            </p>
            <LocationPicker
              lat={values.locationLat}
              lng={values.locationLng}
              onChange={(lat, lng) => onChange({ locationLat: lat, locationLng: lng })}
              placeholder={t('address.search_location')}
            />
            {errors.locationLat && (
              <p className="mt-1 text-xs text-red-500 font-body">{errors.locationLat}</p>
            )}
          </div>
          <Input
            label={t('common.address_line_1')}
            placeholder={t('checkout.address_placeholder')}
            value={values.addressLine1}
            onChange={field('addressLine1')}
            error={errors.addressLine1}
          />
          <Input
            label={t('common.address_line_2_optional')}
            placeholder={t('checkout.address2_placeholder')}
            value={values.addressLine2}
            onChange={field('addressLine2')}
          />
          <Select
            label={t('address.region')}
            options={REGION_OPTIONS}
            value={values.region}
            onChange={field('region')}
            error={errors.region}
          />
        </div>
      )}
    </div>
  );
}
