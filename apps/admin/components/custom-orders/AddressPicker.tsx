'use client';

/**
 * Where a custom order is going — all of it optional.
 *
 * Laid out like the storefront checkout's AddressModal: the map leads (it
 * answers most of what follows), then the address line it fills, then the
 * flat/villa no map can know. A dropped pin reverse-geocodes into the address
 * line; choosing a search result uses that result's address.
 *
 * The pin is what makes a courier bookable (Slider/Lalamove price and route off
 * it). Without Google Maps configured for the console the address can still be
 * typed, and the order finishes by collection or a third-party courier.
 */

import dynamic from 'next/dynamic';
import { useCallback, useState } from 'react';
import { Input, Spinner } from '@/components/ui';
import { reverseGeocode } from '@/lib/geocode';

const LocationMap = dynamic(() => import('./LocationMap'), {
  ssr: false,
  loading: () => <div className="h-64 bg-gray-100 rounded-sm animate-pulse" />,
});

export interface AddressDraft {
  latitude: number | null;
  longitude: number | null;
  address_line_1: string;
  unit_number: string;
}

export const EMPTY_ADDRESS: AddressDraft = {
  latitude: null,
  longitude: null,
  address_line_1: '',
  unit_number: '',
};

/** The request shape, or `null` when nothing was entered at all. */
export function toAddressIn(a: AddressDraft) {
  const line = a.address_line_1.trim();
  const unit = a.unit_number.trim();
  const hasPin = a.latitude !== null && a.longitude !== null;
  if (!hasPin && !line && !unit) return null;
  return {
    latitude: hasPin ? a.latitude : null,
    longitude: hasPin ? a.longitude : null,
    address_line_1: line || null,
    unit_number: unit || null,
  };
}

export function addressDraftFrom(
  a: { latitude: number | null; longitude: number | null; address_line_1: string | null; unit_number: string | null } | null,
): AddressDraft {
  if (!a) return EMPTY_ADDRESS;
  return {
    latitude: a.latitude,
    longitude: a.longitude,
    address_line_1: a.address_line_1 ?? '',
    unit_number: a.unit_number ?? '',
  };
}

export function AddressPicker({
  value,
  onChange,
  disabled,
  idPrefix = 'address',
}: {
  value: AddressDraft;
  onChange: (next: AddressDraft) => void;
  disabled?: boolean;
  /** Keeps label/input ids unique when two of these share a page. */
  idPrefix?: string;
}) {
  const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? '';
  const [geocoding, setGeocoding] = useState(false);
  const hasPin = value.latitude !== null && value.longitude !== null;

  // The pin is the statement of where the cake goes, so it wins over whatever
  // the line said before — and the line stays editable underneath.
  const handlePin = useCallback(
    async (lat: number, lng: number, selectedAddress?: string) => {
      const base = { ...value, latitude: lat, longitude: lng };
      if (selectedAddress) {
        onChange({ ...base, address_line_1: selectedAddress });
        return;
      }
      onChange(base);
      setGeocoding(true);
      const found = await reverseGeocode(lat, lng);
      setGeocoding(false);
      if (found) onChange({ ...base, address_line_1: found.address });
    },
    [value, onChange],
  );

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-baseline justify-between mb-2">
          <p className="text-xs font-medium uppercase tracking-wider text-gray-600">Pin location</p>
          {hasPin && !disabled && (
            <button
              type="button"
              onClick={() => onChange({ ...value, latitude: null, longitude: null })}
              className="text-xs font-body text-gray-500 underline underline-offset-2 hover:text-primary"
            >
              Remove pin
            </button>
          )}
        </div>
        {!apiKey ? (
          <div className="border border-dashed border-gray-300 bg-gray-50 px-4 py-3 text-xs font-body text-gray-500">
            Google Maps isn&rsquo;t configured for the console
            (<code>NEXT_PUBLIC_GOOGLE_MAPS_API_KEY</code>), so no pin can be dropped here. Type the
            address below; without a pin the order finishes by customer collection or a
            third-party courier.
            {hasPin && (
              <span className="block mt-1 text-gray-600">
                A pin is already set ({value.latitude?.toFixed(5)}, {value.longitude?.toFixed(5)}).
              </span>
            )}
          </div>
        ) : disabled ? (
          <p className="text-xs font-body text-gray-500">
            {hasPin
              ? `Pinned at ${value.latitude?.toFixed(5)}, ${value.longitude?.toFixed(5)}.`
              : 'No pin.'}
          </p>
        ) : (
          <LocationMap
            apiKey={apiKey}
            lat={value.latitude}
            lng={value.longitude}
            onChange={handlePin}
            placeholder="Search for the delivery location…"
          />
        )}
        {geocoding && (
          <p className="mt-2 flex items-center gap-2 text-xs text-gray-400 font-body">
            <Spinner className="w-3 h-3" /> Finding the address…
          </p>
        )}
      </div>

      <Input
        id={`${idPrefix}-line`}
        label="Address"
        placeholder="Building, street, area"
        value={value.address_line_1}
        maxLength={255}
        disabled={disabled}
        onChange={e => onChange({ ...value, address_line_1: e.target.value })}
      />
      <Input
        id={`${idPrefix}-unit`}
        label="Flat / villa"
        placeholder="e.g. Flat 1203, or Villa 14"
        value={value.unit_number}
        maxLength={50}
        disabled={disabled}
        onChange={e => onChange({ ...value, unit_number: e.target.value })}
      />
    </div>
  );
}
