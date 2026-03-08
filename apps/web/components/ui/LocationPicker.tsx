'use client';

import { useCallback, useRef, useState } from 'react';
import { GoogleMap, Marker, StandaloneSearchBox, useJsApiLoader } from '@react-google-maps/api';

const DUBAI_CENTER = { lat: 25.2048, lng: 55.2708 };
const LIBRARIES: ('places')[] = ['places'];

interface LocationPickerProps {
  lat: number | null;
  lng: number | null;
  onChange: (lat: number, lng: number) => void;
}

export function LocationPicker({ lat, lng, onChange }: LocationPickerProps) {
  const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? '';
  const { isLoaded, loadError } = useJsApiLoader({
    googleMapsApiKey: apiKey,
    libraries: LIBRARIES,
  });

  const searchBoxRef = useRef<google.maps.places.SearchBox | null>(null);
  const [mapCenter, setMapCenter] = useState(
    lat !== null && lng !== null ? { lat, lng } : DUBAI_CENTER
  );

  const handleMapClick = useCallback(
    (e: google.maps.MapMouseEvent) => {
      if (!e.latLng) return;
      const newLat = e.latLng.lat();
      const newLng = e.latLng.lng();
      setMapCenter({ lat: newLat, lng: newLng });
      onChange(newLat, newLng);
    },
    [onChange]
  );

  const handlePlacesChanged = useCallback(() => {
    if (!searchBoxRef.current) return;
    const places = searchBoxRef.current.getPlaces();
    if (!places || places.length === 0) return;
    const place = places[0];
    const location = place.geometry?.location;
    if (!location) return;
    const newLat = location.lat();
    const newLng = location.lng();
    setMapCenter({ lat: newLat, lng: newLng });
    onChange(newLat, newLng);
  }, [onChange]);

  if (!apiKey) {
    return (
      <div className="h-48 flex items-center justify-center bg-gray-50 border border-dashed border-gray-200 rounded-sm">
        <p className="text-xs text-gray-400 font-body">Google Maps not configured</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="h-48 flex items-center justify-center bg-gray-50 border border-dashed border-gray-200 rounded-sm">
        <p className="text-xs text-red-400 font-body">Failed to load map</p>
      </div>
    );
  }

  if (!isLoaded) {
    return (
      <div className="h-48 flex items-center justify-center bg-gray-50 border border-dashed border-gray-200 rounded-sm animate-pulse">
        <p className="text-xs text-gray-400 font-body">Loading map…</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <StandaloneSearchBox
        onLoad={(ref) => { searchBoxRef.current = ref; }}
        onPlacesChanged={handlePlacesChanged}
      >
        <input
          type="text"
          placeholder="Search for a location…"
          className="w-full px-3.5 py-2.5 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
        />
      </StandaloneSearchBox>

      <GoogleMap
        mapContainerStyle={{ width: '100%', height: '200px', borderRadius: '2px' }}
        center={mapCenter}
        zoom={13}
        onClick={handleMapClick}
        options={{
          disableDefaultUI: true,
          zoomControl: true,
          streetViewControl: false,
          fullscreenControl: false,
        }}
      >
        {lat !== null && lng !== null && (
          <Marker
            position={{ lat, lng }}
            draggable
            onDragEnd={(e) => {
              if (!e.latLng) return;
              onChange(e.latLng.lat(), e.latLng.lng());
            }}
          />
        )}
      </GoogleMap>

      {lat !== null && lng !== null && (
        <p className="text-xs text-gray-400 font-body">
          {lat.toFixed(6)}, {lng.toFixed(6)}
        </p>
      )}
    </div>
  );
}
