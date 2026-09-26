'use client';

/**
 * A Google map with a place search and a draggable pin — the console's port of
 * the storefront's `components/ui/LocationPicker.tsx`.
 *
 * Differences from the storefront one, each deliberate:
 * - no "use my current location": the console's `Permissions-Policy` denies
 *   geolocation (next.config.ts), and staff are rarely where the cake is going;
 * - no analytics events (the console has none);
 * - the key and map id are read here and the caller (`AddressPicker`) decides
 *   what to show when there is no key, so this file assumes it has one.
 *
 * Loaded through `next/dynamic` with `ssr: false` so the Maps library stays out
 * of every other console page's bundle.
 */

import { useCallback, useEffect, useRef } from 'react';
import {
  APIProvider,
  AdvancedMarker,
  Map,
  Marker,
  useMap,
  useMapsLibrary,
  type MapMouseEvent,
} from '@vis.gl/react-google-maps';

const SHARJAH_CENTER = { lat: 25.3304139, lng: 55.3736131 };
const MAP_ID = process.env.NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID || undefined;

function formatSelectedAddress(place: google.maps.places.Place): string | undefined {
  const name = place.displayName?.trim();
  const formattedAddress = place.formattedAddress?.trim();
  // Google returns the UAE's addresses bilingually ("Al Majaz 3 - المجاز 3");
  // the rider reads the English half.
  const englishParts = formattedAddress
    ?.split(/\s+-\s+/)
    .map(part => part.replace(/[؀-ۿ]/g, '').trim())
    .map(part => part.replace(/^(.+?)(\1)\s*(\d+)?$/, '$1 $3').trim())
    .filter(part => /[A-Za-z0-9]/.test(part))
    .filter(
      (part, index, parts) =>
        parts.findIndex(
          candidate => candidate.localeCompare(part, undefined, { sensitivity: 'accent' }) === 0,
        ) === index,
    );
  const address = englishParts?.join(', ') || formattedAddress;
  if (!address) return name || undefined;
  if (!name || address.toLocaleLowerCase().includes(name.toLocaleLowerCase())) return address;
  return `${name}, ${address}`;
}

export interface LocationMapProps {
  apiKey: string;
  lat: number | null;
  lng: number | null;
  /** `selectedAddress` is present only when a search result was chosen. */
  onChange: (lat: number, lng: number, selectedAddress?: string) => void;
  placeholder?: string;
  height?: string;
}

function MapContent({
  lat,
  lng,
  onChange,
  placeholder,
  height = '260px',
}: Omit<LocationMapProps, 'apiKey'>) {
  const map = useMap();
  const placesLib = useMapsLibrary('places');
  const containerRef = useRef<HTMLDivElement>(null);

  // Stable refs so the autocomplete element is not torn down on every render.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });
  const mapRef = useRef(map);
  useEffect(() => {
    mapRef.current = map;
  });

  const position = lat !== null && lng !== null ? { lat, lng } : null;

  // Keep the pin on screen when it moves for a reason other than a tap (an
  // existing address opening for edit), without fighting a tap near the edge.
  useEffect(() => {
    if (!map || !position) return;
    const bounds = map.getBounds();
    if (!bounds || !bounds.contains(position)) map.panTo(position);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, position?.lat, position?.lng]);

  useEffect(() => {
    const container = containerRef.current;
    if (!placesLib || !container) return;
    container.innerHTML = '';

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const PlaceAutocompleteElement = (placesLib as any).PlaceAutocompleteElement;
    if (!PlaceAutocompleteElement) return;

    // The shop delivers only within the UAE.
    const placeAc = new PlaceAutocompleteElement({ includedRegionCodes: ['AE'] });
    placeAc.setAttribute('placeholder', placeholder ?? '');
    container.appendChild(placeAc);

    const handler = async (event: Event) => {
      // `gmp-select` carries a PlacePrediction that must become a Place before
      // its location can be fetched (Maps v3.59.8+).
      const place = (
        event as Event & { placePrediction?: { toPlace: () => google.maps.places.Place } }
      ).placePrediction?.toPlace();
      if (!place) return;
      await place.fetchFields({ fields: ['displayName', 'formattedAddress', 'location'] });
      const loc = place.location;
      if (!loc) return;
      onChangeRef.current(loc.lat(), loc.lng(), formatSelectedAddress(place));
      mapRef.current?.panTo({ lat: loc.lat(), lng: loc.lng() });
      mapRef.current?.setZoom(15);
    };
    placeAc.addEventListener('gmp-select', handler);
    return () => {
      placeAc.removeEventListener('gmp-select', handler);
      container.innerHTML = '';
    };
  }, [placesLib, placeholder]);

  const handleMapClick = useCallback(
    (e: MapMouseEvent) => {
      if (!e.detail.latLng) return;
      onChange(e.detail.latLng.lat, e.detail.latLng.lng);
    },
    [onChange],
  );

  return (
    <>
      <div ref={containerRef} className="w-full" />
      <p className="font-body text-xs text-gray-400">
        {position ? 'Pin set — drag it to adjust.' : 'Search above, or click the map to drop a pin.'}
      </p>
      <Map
        style={{ width: '100%', height, borderRadius: '2px' }}
        defaultCenter={position ?? SHARJAH_CENTER}
        defaultZoom={12}
        mapId={MAP_ID}
        disableDefaultUI
        zoomControl
        gestureHandling="greedy"
        onClick={handleMapClick}
      >
        {/* An advanced marker needs a map id; without one the classic marker
            still drops and drags, so a missing id costs styling, not the pin. */}
        {position && MAP_ID && (
          <AdvancedMarker
            position={position}
            draggable
            onDragEnd={e => {
              if (!e.latLng) return;
              onChange(e.latLng.lat(), e.latLng.lng());
            }}
          />
        )}
        {position && !MAP_ID && (
          <Marker
            position={position}
            draggable
            onDragEnd={e => {
              if (!e.latLng) return;
              onChange(e.latLng.lat(), e.latLng.lng());
            }}
          />
        )}
      </Map>
    </>
  );
}

export default function LocationMap({ apiKey, ...props }: LocationMapProps) {
  return (
    <div className="space-y-2">
      <APIProvider apiKey={apiKey} libraries={['places']}>
        <MapContent {...props} />
      </APIProvider>
    </div>
  );
}
