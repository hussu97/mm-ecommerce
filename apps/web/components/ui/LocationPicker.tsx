'use client';

import { useCallback, useEffect, useRef } from 'react';
import { APIProvider, Map, AdvancedMarker, useMapsLibrary, useMap, type MapMouseEvent } from '@vis.gl/react-google-maps';

import { analytics } from '@/lib/analytics';

const DUBAI_CENTER = { lat: 25.2048, lng: 55.2708 };
const MELTING_MOMENTS_CAKES = {
  lat: 25.3304139,
  lng: 55.3736131,
  address: 'Melting Moments Cakes, Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah',
};

function isMeltingMomentsCakes(place: google.maps.places.Place): boolean {
  const location = place.location;
  return place.displayName?.trim().toLocaleLowerCase() === 'melting moments cakes'
    && location !== null
    && location !== undefined
    && Math.abs(location.lat() - MELTING_MOMENTS_CAKES.lat) < 0.0001
    && Math.abs(location.lng() - MELTING_MOMENTS_CAKES.lng) < 0.0001;
}

function formatSelectedAddress(place: google.maps.places.Place): string | undefined {
  if (isMeltingMomentsCakes(place)) return MELTING_MOMENTS_CAKES.address;
  const name = place.displayName?.trim();
  const formattedAddress = place.formattedAddress?.trim();
  const englishParts = formattedAddress
    ?.split(/\s+-\s+/)
    .map((part) => part.replace(/[\u0600-\u06FF]/g, '').trim())
    .map((part) => part.replace(/^(.+?)(\1)\s*(\d+)?$/, '$1 $3').trim())
    .filter((part) => /[A-Za-z0-9]/.test(part))
    .filter((part, index, parts) => parts.findIndex(
      (candidate) => candidate.localeCompare(part, undefined, { sensitivity: 'accent' }) === 0,
    ) === index);
  const address = englishParts?.join(', ') || formattedAddress;
  if (!address) return name || undefined;
  if (!name || address.toLocaleLowerCase().includes(name.toLocaleLowerCase())) return address;
  return `${name}, ${address}`;
}

interface LocationPickerProps {
  lat: number | null;
  lng: number | null;
  /** Present only when the customer selects an autocomplete result. */
  onChange: (lat: number, lng: number, selectedAddress?: string) => void;
  placeholder?: string;
  /** Map height. The checkout modal gives the map real estate to be usable
   *  with a thumb; inline uses stay compact. */
  height?: string;
}

// ─── Inner component (must live inside APIProvider) ───────────────────────────

function MapContent({ lat, lng, onChange, placeholder, height = '200px' }: LocationPickerProps) {
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

  /**
   * Keep the pin on screen whenever it moves for a reason other than a tap.
   *
   * This used to fire only when the map instance appeared, so the viewport was
   * whatever `defaultCenter` happened to be at mount and never moved again.
   * Opening a saved address for editing then showed the map over Dubai (or over
   * the address edited before it) with that address's marker somewhere off the
   * edge — the pin was right, the thing the customer was looking at was not.
   *
   * Panning only when the pin has left the viewport is what keeps this from
   * fighting the customer: tapping near an edge of the map moves the pin, and
   * re-centring on every tap would drag the map out from under their finger.
   */
  useEffect(() => {
    if (!map || !position) return;
    const bounds = map.getBounds();
    if (!bounds || !bounds.contains(position)) map.panTo(position);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, position?.lat, position?.lng]);

  // Wire up PlaceAutocompleteElement once the library is ready.
  // Deps exclude onChange/map so the element isn't torn down on every render.
  useEffect(() => {
    const container = containerRef.current;
    if (!placesLib || !container) return;

    container.innerHTML = '';

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const PlaceAutocompleteElement = (placesLib as any).PlaceAutocompleteElement;
    if (!PlaceAutocompleteElement) return;

    // We deliver only within the UAE, so international predictions are not
    // actionable and make the address picker slower to use.
    const placeAc = new PlaceAutocompleteElement({ includedRegionCodes: ['AE'] });
    placeAc.setAttribute('placeholder', placeholder ?? '');
    container.appendChild(placeAc);

    const handler = async (event: Event) => {
      // Maps v3.59.8 renamed `gmp-placeselect` to `gmp-select`. The new
      // event carries a PlacePrediction, which must become a Place before its
      // location can be fetched. Listening to the retired event left the pin
      // and Address Line 1 unchanged after a customer chose a result.
      const place = (event as Event & {
        placePrediction?: { toPlace: () => google.maps.places.Place };
      }).placePrediction?.toPlace();
      if (!place) return;
      await place.fetchFields({ fields: ['displayName', 'formattedAddress', 'location'] });
      const loc = place.location;
      if (!loc) return;
      const newLat = loc.lat();
      const newLng = loc.lng();
      // How the pin got where it is. Four routes to the same field, and which
      // one people actually use is what decides whether the map is worth its
      // size on a phone — or whether search alone would do.
      analytics.locationPinSet({ method: 'autocomplete', surface: 'checkout' });
      onChangeRef.current(newLat, newLng, formatSelectedAddress(place));
      mapRef.current?.panTo({ lat: newLat, lng: newLng });
      mapRef.current?.setZoom(15);
    };

    /**
     * Keep the box you are typing in on the screen.
     *
     * The picker lives inside a scrollable modal, and the suggestion list is
     * tall. On a phone the keyboard then takes half the screen: the browser
     * scrolls *something* into view, the list wins, and the input itself ends
     * up above the top edge — so the customer is choosing between addresses
     * without being able to see what they searched for.
     *
     * Scrolling the input to the top of its scroller leaves the whole list
     * below it and the input visible. Deferred because the keyboard has not
     * finished animating when `focus` fires, and scrolling into a viewport
     * that is about to halve in height puts it back where it started.
     */
    const keepVisible = () => {
      setTimeout(() => {
        container.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 350);
    };
    placeAc.addEventListener('focusin', keepVisible);
    placeAc.addEventListener('gmp-select', handler);

    return () => {
      placeAc.removeEventListener('focusin', keepVisible);
      placeAc.removeEventListener('gmp-select', handler);
      container.innerHTML = '';
    };
  }, [placesLib, placeholder]);

  const handleMapClick = useCallback(
    (e: MapMouseEvent) => {
      if (!e.detail.latLng) return;
      analytics.locationPinSet({ method: 'map_tap', surface: 'checkout' });
      onChange(e.detail.latLng.lat, e.detail.latLng.lng);
    },
    [onChange]
  );

  // One tap beats pinching a 200px map on a phone, which is how most of this
  // traffic arrives. Silently ignored if the browser denies permission — the
  // pin is a convenience for the driver, never a gate on the order.
  const useCurrentLocation = useCallback(() => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        analytics.locationPinSet({ method: 'current_location', surface: 'checkout' });
        onChangeRef.current(latitude, longitude);
        mapRef.current?.panTo({ lat: latitude, lng: longitude });
        mapRef.current?.setZoom(16);
      },
      () => {
        // Denied or unavailable — the map is still there to tap. Worth counting
        // even so: a high refusal rate is the reason the one-tap shortcut on
        // this form looks unused.
        analytics.geolocationDenied({ surface: 'checkout' });
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    );
  }, []);

  return (
    <>
      <div ref={containerRef} className="w-full" />

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="font-body text-xs text-gray-400">
          {position ? 'Pin set — drag it to adjust.' : 'Search above, or tap the map to drop a pin.'}
        </p>
        <button
          type="button"
          onClick={useCurrentLocation}
          className="inline-flex items-center gap-1.5 text-xs font-body text-primary hover:underline"
        >
          <span className="material-icons text-sm">my_location</span>
          Use my current location
        </button>
      </div>

      <Map
        style={{ width: '100%', height, borderRadius: '2px' }}
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
              if (!e.latLng) return;
              analytics.locationPinSet({ method: 'drag', surface: 'checkout' });
              onChange(e.latLng.lat(), e.latLng.lng());
            }}
          />
        )}
      </Map>
    </>
  );
}

// ─── Public component ─────────────────────────────────────────────────────────

export function LocationPicker({
  lat, lng, onChange, placeholder = 'Search for a location…', height,
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
        <MapContent lat={lat} lng={lng} onChange={onChange} placeholder={placeholder} height={height} />
      </APIProvider>
    </div>
  );
}
