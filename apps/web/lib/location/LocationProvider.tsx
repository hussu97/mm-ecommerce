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

import { usePathname, useRouter } from 'next/navigation';

import { addressesApi, deliveryApi } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { guestAddresses } from '@/lib/guest-addresses';
import { readBranch, rememberBranch } from './branch-cookie';
import { MAX_FIX_AGE_MS, firstPinnedAddress, shouldReplaceWithBrowserFix } from './refresh';
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
  /**
   * Re-read the customer's default address and move the location to it.
   *
   * Call after saving, deleting or re-defaulting an address. Resolution runs
   * per sign-in and prefers whatever is in `localStorage`, which is right for a
   * page load and wrong for the moment somebody changes where they live:
   * without this the new default was invisible until the browser storage was
   * cleared, and every delivery estimate on the site kept answering for the old
   * address.
   */
  refreshFromAddresses: () => Promise<void>;
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

function forget() {
  try {
    window.localStorage.removeItem(LOCATION_STORAGE_KEY);
  } catch {
    /* storage unavailable — the in-memory reset below is what actually matters */
  }
}

/**
 * Whether this browser has already said yes, without asking it anything.
 *
 * The Permissions API is the only way to find out; `getCurrentPosition` itself
 * answers the question by prompting, which is exactly what a background
 * refresh must never do. A browser without the API therefore reports "not
 * granted" and simply never refreshes silently — the safe direction to fail,
 * because the other one is an unexplained permission prompt on page load.
 */
async function geolocationGranted(): Promise<boolean> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) return false;
  if (!navigator.permissions?.query) return false;
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' });
    return status.state === 'granted';
  } catch {
    // Some browsers reject the descriptor rather than support it. Same answer.
    return false;
  }
}

export function LocationProvider({ children }: { children: React.ReactNode }) {
  const { user, isLoading: authLoading } = useAuth();
  const [location, setLocationState] = useState<Location>(DEFAULT_LOCATION);
  const [area, setArea] = useState<DeliveryArea | null>(null);
  const router = useRouter();
  const pathname = usePathname();
  const [loading, setLoading] = useState(false);

  /**
   * Which account the location on screen was resolved for.
   *
   * `undefined` means never resolved, `null` means resolved as a signed-out
   * visitor, and a string is a user id. Comparing against it is what makes
   * resolution re-run on sign-in and sign-out and *only* then: a plain boolean
   * ran once for the lifetime of the tab, so signing in never promoted the
   * account's saved address, and signing out left the previous customer's
   * neighbourhood on screen for whoever used the device next.
   */
  const seededFor = useRef<string | null | undefined>(undefined);

  /** True once resolution has settled, so the refresh below cannot race it. */
  const [resolved, setResolved] = useState(false);

  /**
   * The current pin, readable from a callback that must not re-create itself
   * every time it moves. Every write goes through `setLocation`, so this and
   * the state cannot drift.
   */
  const locationRef = useRef<Location>(DEFAULT_LOCATION);

  const setLocation = useCallback((next: Location) => {
    locationRef.current = next;
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
          // No distance guard here, unlike the silent refresh: somebody who has
          // just pressed "use my location" is owed the reading they asked for.
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

  /**
   * Take a fresh reading, but only from a browser that has already agreed to
   * give one, and only when it changes the answer.
   *
   * This is what stops the pin freezing on the day it was first set. A reading
   * is a snapshot of where a handset was at one moment, and we were storing it
   * and then treating it as settled fact forever — the customer moved emirate
   * and the site went on quoting Sharjah's free-delivery threshold at them.
   * Resolves true when the pin actually moved.
   */
  const refreshFromBrowser = useCallback(async () => {
    if (!(await geolocationGranted())) return false;
    return new Promise<boolean>((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const fix = {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
          };
          if (!shouldReplaceWithBrowserFix(locationRef.current, fix)) {
            resolve(false);
            return;
          }
          setLocation({ ...fix, source: 'geolocation', label: null });
          resolve(true);
        },
        // Granted and still failed — no signal indoors, radio off. Keep what
        // we have; a stale pin beats none.
        () => resolve(false),
        { enableHighAccuracy: false, timeout: 10_000, maximumAge: MAX_FIX_AGE_MS },
      );
    });
  }, [setLocation]);

  const refreshFromAddresses = useCallback(async () => {
    try {
      const preferred = firstPinnedAddress(await addressesApi.list());
      if (!preferred) return;
      setLocation({
        latitude: Number(preferred.latitude),
        longitude: Number(preferred.longitude),
        source: 'address',
        label: preferred.label ?? null,
      });
    } catch {
      /* not signed in, or the call failed — keep whatever we had */
    }
  }, [setLocation]);

  // ── resolve, best source first, and again whenever the account changes ─────
  //
  // Order matters and is the opposite of "most recent wins". A saved address is
  // the strongest signal we have — the customer typed it and orders go there —
  // so it beats a browser reading, which is only ever where the handset happens
  // to be right now. Asking the browser is last because it costs a permission
  // prompt, and prompting somebody whose address we already know is a prompt
  // that buys nothing.
  useEffect(() => {
    // Nothing may be resolved until we know whether there is an account,
    // because the answer changes which source wins. `user` is null on the first
    // render of every page load — the session is restored a beat later — so
    // resolving on it would mean a signed-in customer's saved address never
    // won, and every one of them met a geolocation prompt for a location we
    // already had on file.
    if (authLoading) return;

    const identity = user?.id ?? null;
    if (seededFor.current === identity) return;
    const previous = seededFor.current;
    const firstRun = previous === undefined;
    seededFor.current = identity;

    let cancelled = false;
    let completed = false;

    (async () => {
      let stored = readStored();

      // Signing out takes the account's address book with it, and a pin derived
      // from one is that customer's home. It must not survive into whoever uses
      // the device next. A pin the *device* owns — its own browser reading, its
      // own dropped marker — is not the account's to take away.
      if (!firstRun && !user && stored?.source === 'address') {
        forget();
        stored = null;
        setLocationState(DEFAULT_LOCATION);
        locationRef.current = DEFAULT_LOCATION;
      }

      // A page load trusts what it already knows. A sign-in does not: the
      // account that just arrived may name a stronger source than whatever the
      // visitor was browsing with, and finding that out is the point.
      if (firstRun && stored) {
        setLocation(stored);
        return;
      }

      if (user) {
        try {
          const preferred = firstPinnedAddress(await addressesApi.list());
          if (cancelled) return;
          if (preferred) {
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
        if (cancelled) return;
      }

      const lastOrdered = firstPinnedAddress(guestAddresses.list());
      if (lastOrdered) {
        setLocation({
          latitude: Number(lastOrdered.latitude),
          longitude: Number(lastOrdered.longitude),
          source: 'address',
          label: lastOrdered.label ?? null,
        });
        return;
      }

      // No address anywhere. Someone signing in to an account with an empty
      // address book keeps the pin they were already browsing with rather than
      // being thrown back to the shop's coordinates for the crime of logging
      // in — but a reading is still worth taking, since it may have moved.
      if (stored) setLocation(stored);

      // A browser that has already granted permission can answer without a
      // prompt, and does so however we got here.
      if (await refreshFromBrowser()) return;
      if (cancelled || stored) return;

      // Asking outright is reserved for a page load. Springing a permission
      // dialog on somebody the instant they press "log out" is a prompt nobody
      // invited, and the banner's own "use my location" button is right there
      // for whoever does want it.
      if (!firstRun) return;

      // Asked once, ever — `LOCATION_ASKED_KEY` is what makes it once rather
      // than every visit, because asking again on every visit is how a
      // permission prompt turns into a reason to leave.

      let asked = false;
      try {
        asked = window.localStorage.getItem(LOCATION_ASKED_KEY) === '1';
      } catch {
        /* treat unreadable storage as "not asked" */
      }
      if (!asked && !cancelled) {
        await requestBrowserLocation();
      }
    })().finally(() => {
      completed = true;
      if (!cancelled) setResolved(true);
    });

    return () => {
      cancelled = true;
      // React's development double-invoke tears this effect down between its
      // two runs. Without rolling the marker back, the cancelled first run
      // would be the only one that ever happened and nothing would resolve.
      if (!completed) seededFor.current = previous;
    };
  }, [authLoading, user, setLocation, refreshFromBrowser, requestBrowserLocation]);

  // ── a new screen is a new chance to notice they have moved ─────────────────
  //
  // Costs nothing when they have not: `maximumAge` lets the browser answer from
  // its own cache, and a reading inside `MOVE_THRESHOLD_M` of the current pin
  // is dropped without touching state, so no delivery lookup and no re-render.
  // Gated on `resolved` so it cannot race the chain above and overwrite an
  // address that is still in flight.
  useEffect(() => {
    if (!resolved) return;
    void refreshFromBrowser();
  }, [pathname, resolved, refreshFromBrowser]);

  /**
   * Record the kitchen, and redraw the page when it has actually changed.
   *
   * The cookie alone only reaches the *next* server render, which for somebody
   * setting their location while looking at a category page means the grid goes
   * on showing another branch's shelf until they navigate. `router.refresh()`
   * re-runs the server components in place, with the new cookie.
   *
   * Guarded on the value rather than fired on every lookup, and that guard is
   * load-bearing: this effect runs whenever the pin moves, and refreshing
   * unconditionally would re-render the tree on every one of them — including
   * the several that resolve to the same kitchen.
   */
  const applyBranch = useCallback(
    (branchId: string | null) => {
      if (readBranch() === branchId) return;
      rememberBranch(branchId);
      router.refresh();
    },
    [router],
  );

  // ── what delivery looks like there ─────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    deliveryApi
      .area(location.latitude, location.longitude)
      .then((result) => {
        if (cancelled) return;
        setArea(result);
        // The one piece of this the *server* needs. The category grid, the
        // homepage rail and search are rendered server-side and filter the
        // catalogue to the kitchen that would bake this order, and a cookie is
        // the only browser state they can read — see `branch-cookie.ts`.
        applyBranch(result.branch_id ?? null);
      })
      .catch(() => {
        // The banner falls back to its unlocated copy. A failed lookup must not
        // leave a stale promise about a different emirate on screen — and for
        // the same reason it must not leave a branch behind: a catalogue
        // filtered by a kitchen we are no longer sure about is a shelf that
        // does not match anything.
        if (cancelled) return;
        setArea(null);
        applyBranch(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // `applyBranch` is stable on `router` and included so the linter can see
    // the effect's whole surface; the pin is what actually re-runs it.
  }, [location.latitude, location.longitude, applyBranch]);

  const value = useMemo<LocationContextValue>(
    () => ({
      location,
      area,
      loading,
      isKnown: location.source !== 'default',
      setLocation,
      requestBrowserLocation,
      refreshFromAddresses,
    }),
    [location, area, loading, setLocation, requestBrowserLocation, refreshFromAddresses],
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
