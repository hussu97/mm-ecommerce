'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { addressesApi, deliveryApi } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { guestAddresses } from '@/lib/guest-addresses';
import {
  DEFAULT_LOCATION,
  LOCATION_ASKED_KEY,
  LOCATION_STORAGE_KEY,
  type DeliveryArea,
  type Location,
} from './types';

interface LocationContextValue {
  /** Never null. Falls back to the shop, with `source: 'default'` saying so. */
  location: Location;
  /** What delivery looks like there. Null until the first lookup lands. */
  area: DeliveryArea | null;
  /** True while a lookup is in flight, so a changing number can say so. */
  loading: boolean;
  /** Whether we are showing a real place or the shop standing in for one. */
  isKnown: boolean;
  setLocation: (next: Location) => void;
  /** Ask the browser. Safe to call when already asked; it just re-prompts. */
  requestBrowserLocation: () => Promise<boolean>;
}

const LocationContext = createContext<LocationContextValue | null>(null);

function readStored(): Location | null {
  try {
    const raw = window.localStorage.getItem(LOCATION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Location;
    if (typeof parsed?.latitude !== 'number' || typeof parsed?.longitude !== 'number') {
      return null;
    }
    return parsed;
  } catch {
    // A corrupt entry, private mode, storage disabled. None of it is worth a
    // broken homepage — we simply do not know where they are.
    return null;
  }
}

function persist(location: Location) {
  try {
    window.localStorage.setItem(LOCATION_STORAGE_KEY, JSON.stringify(location));
  } catch {
    /* storage unavailable — the location still works for this page view */
  }
}

export function LocationProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [location, setLocationState] = useState<Location>(DEFAULT_LOCATION);
  const [area, setArea] = useState<DeliveryArea | null>(null);
  const [loading, setLoading] = useState(false);

  // Guards the seed so it runs once. Without it, `user` arriving a beat after
  // mount re-runs the whole chain and can overwrite a location the customer
  // has already changed by hand.
  const seeded = useRef(false);

  const setLocation = useCallback((next: Location) => {
    setLocationState(next);
    persist(next);
  }, []);

  const requestBrowserLocation = useCallback(async () => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) return false;
    try {
      window.localStorage.setItem(LOCATION_ASKED_KEY, '1');
    } catch {
      /* not worth failing the prompt over */
    }
    return new Promise<boolean>((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setLocation({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            source: 'geolocation',
            label: null,
          });
          resolve(true);
        },
        // Declining is a normal answer, not an error. We keep whatever we had.
        () => resolve(false),
        { enableHighAccuracy: false, timeout: 10_000, maximumAge: 600_000 },
      );
    });
  }, [setLocation]);

  // ── seed, best source first ────────────────────────────────────────────────
  //
  // Order matters and is the opposite of "most recent wins". A saved address is
  // the strongest signal we have — the customer typed it and orders go there —
  // so it beats a browser reading, which is only ever where the handset happens
  // to be right now. Asking the browser is last because it costs a permission
  // prompt, and prompting somebody whose address we already know is a prompt
  // that buys nothing.
  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;

    let cancelled = false;

    (async () => {
      const stored = readStored();
      if (stored) {
        setLocationState(stored);
        return;
      }

      if (user) {
        try {
          const addresses = await addressesApi.list();
          const preferred =
            addresses.find((a) => a.is_default) ?? addresses[0] ?? null;
          if (preferred && !cancelled) {
            setLocation({
              latitude: Number(preferred.latitude),
              longitude: Number(preferred.longitude),
              source: 'address',
              label: preferred.label ?? null,
            });
            return;
          }
        } catch {
          /* not signed in after all, or the call failed — try the next source */
        }
      }

      const guest = guestAddresses.list();
      const lastOrdered = guest.find((a) => a.is_default) ?? guest[0] ?? null;
      if (lastOrdered && !cancelled) {
        setLocation({
          latitude: Number(lastOrdered.latitude),
          longitude: Number(lastOrdered.longitude),
          source: 'address',
          label: lastOrdered.label ?? null,
        });
        return;
      }

      // Nothing on file. Ask once, ever — `LOCATION_ASKED_KEY` is what makes it
      // once rather than every visit.
      let asked = false;
      try {
        asked = window.localStorage.getItem(LOCATION_ASKED_KEY) === '1';
      } catch {
        /* treat unreadable storage as "not asked" */
      }
      if (!asked && !cancelled) {
        await requestBrowserLocation();
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user, setLocation, requestBrowserLocation]);

  // ── what delivery looks like there ─────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    deliveryApi
      .area(location.latitude, location.longitude)
      .then((result) => {
        if (!cancelled) setArea(result);
      })
      .catch(() => {
        // The banner falls back to its unlocated copy. A failed lookup must not
        // leave a stale promise about a different emirate on screen.
        if (!cancelled) setArea(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [location.latitude, location.longitude]);

  const value = useMemo<LocationContextValue>(
    () => ({
      location,
      area,
      loading,
      isKnown: location.source !== 'default',
      setLocation,
      requestBrowserLocation,
    }),
    [location, area, loading, setLocation, requestBrowserLocation],
  );

  return <LocationContext.Provider value={value}>{children}</LocationContext.Provider>;
}

export function useLocation(): LocationContextValue {
  const context = useContext(LocationContext);
  if (!context) {
    throw new Error('useLocation must be used inside <LocationProvider>');
  }
  return context;
}
