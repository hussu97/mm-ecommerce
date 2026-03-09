'use client';

import { useCallback, useEffect, useRef } from 'react';
import { APIProvider, Map, AdvancedMarker, useMapsLibrary, useMap, type MapMouseEvent } from '@vis.gl/react-google-maps';

const DUBAI_CENTER = { lat: 25.2048, lng: 55.2708 };

interface LocationPickerProps {
  lat: number | null;
  lng: number | null;
  onChange: (lat: number, lng: number) => void;
  placeholder?: string;
}

// ─── Inner component (must live inside APIProvider) ───────────────────────────

function MapContent({ lat, lng, onChange, placeholder }: LocationPickerProps) {
  const map = useMap();
  const placesLib = useMapsLibrary('places');
  const containerRef = useRef<HTMLDivElement>(null);

  // Keep a stable ref to onChange so the autocomplete effect never needs to
  // re-run (and tear down the input) just because the parent re-rendered.
  const onChangeRef = useRef(onChange);
  useEffect(() => { onChangeRef.current = onChange; });

  const mapRef = useRef(map);
  useEffect(() => { mapRef.current = map; });

  const position = lat !== null && lng !== null ? { lat, lng } : null;

  // Pan map when position is set externally (e.g. saved address loaded)
  useEffect(() => {
    if (map && position) map.panTo(position);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Wire up PlaceAutocompleteElement once the library is ready.
  // Deps exclude onChange/map so the element isn't torn down on every render.
  useEffect(() => {
    if (!placesLib || !containerRef.current) return;

    containerRef.current.innerHTML = '';

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const PlaceAutocompleteElement = (placesLib as any).PlaceAutocompleteElement;
    if (!PlaceAutocompleteElement) return;

    const placeAc = new PlaceAutocompleteElement({});
    placeAc.setAttribute('placeholder', placeholder ?? '');
    containerRef.current.appendChild(placeAc);

    const handler = async (event: Event) => {
      const { place } = (event as CustomEvent<{ place: google.maps.places.Place }>).detail;
      await place.fetchFields({ fields: ['location'] });
      const loc = place.location;
      if (!loc) return;
      const newLat = loc.lat();
      const newLng = loc.lng();
      onChangeRef.current(newLat, newLng);
      mapRef.current?.panTo({ lat: newLat, lng: newLng });
      mapRef.current?.setZoom(15);
    };

    placeAc.addEventListener('gmp-placeselect', handler);

    return () => {
      placeAc.removeEventListener('gmp-placeselect', handler);
      if (containerRef.current) containerRef.current.innerHTML = '';
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placesLib, placeholder]);

  const handleMapClick = useCallback(
    (e: MapMouseEvent) => {
      if (!e.detail.latLng) return;
      onChange(e.detail.latLng.lat, e.detail.latLng.lng);
    },
    [onChange]
  );

  return (
    <>
      <div ref={containerRef} className="w-full" />

      <Map
        style={{ width: '100%', height: '200px', borderRadius: '2px' }}
        defaultCenter={position ?? DUBAI_CENTER}
        defaultZoom={13}
        mapId={process.env.NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID}
        disableDefaultUI
        zoomControl
        gestureHandling="cooperative"
        onClick={handleMapClick}
      >
        {position && (
          <AdvancedMarker
            position={position}
            draggable
            onDragEnd={(e) => {
              if (e.latLng) onChange(e.latLng.lat(), e.latLng.lng());
            }}
          />
        )}
      </Map>
    </>
  );
}

// ─── Public component ─────────────────────────────────────────────────────────

export function LocationPicker({
  lat, lng, onChange, placeholder = 'Search for a location…',
}: LocationPickerProps) {
  const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? '';

  if (!apiKey) {
    return (
      <div className="h-48 flex items-center justify-center bg-gray-50 border border-dashed border-gray-200 rounded-sm">
        <p className="text-xs text-gray-400 font-body">Google Maps not configured</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <APIProvider apiKey={apiKey} libraries={['places']}>
        <MapContent lat={lat} lng={lng} onChange={onChange} placeholder={placeholder} />
      </APIProvider>
    </div>
  );
}
