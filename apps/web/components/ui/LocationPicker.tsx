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
  const inputRef = useRef<HTMLInputElement>(null);

  const position = lat !== null && lng !== null ? { lat, lng } : null;

  // Pan map when position is set externally (e.g. saved address loaded)
  useEffect(() => {
    if (map && position) map.panTo(position);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Wire up Places Autocomplete once the library is ready
  useEffect(() => {
    if (!placesLib || !inputRef.current) return;

    const ac = new placesLib.Autocomplete(inputRef.current, {
      fields: ['geometry'],
    });

    const listener = ac.addListener('place_changed', () => {
      const place = ac.getPlace();
      const loc = place.geometry?.location;
      if (!loc) return;
      const newLat = loc.lat();
      const newLng = loc.lng();
      onChange(newLat, newLng);
      map?.panTo({ lat: newLat, lng: newLng });
      map?.setZoom(15);
    });

    return () => {
      google.maps.event.removeListener(listener);
    };
  }, [placesLib, onChange, map]);

  const handleMapClick = useCallback(
    (e: MapMouseEvent) => {
      if (!e.detail.latLng) return;
      onChange(e.detail.latLng.lat, e.detail.latLng.lng);
    },
    [onChange]
  );

  return (
    <>
      <input
        ref={inputRef}
        type="text"
        placeholder={placeholder}
        className="w-full px-3.5 py-2.5 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
      />

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
