import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';

import type { Address, User } from '@/lib/types';
import { LocationProvider, useLocation } from './LocationProvider';
import {
  LOCATION_ASKED_KEY,
  LOCATION_STORAGE_KEY,
  type DeliveryArea,
  type Location,
} from './types';

// ─── Mocks ────────────────────────────────────────────────────────────────────

const refresh = vi.fn();
// One object, not a fresh literal per call: the real `useRouter` is stable, and
// a router that changed identity every render would re-run the delivery effect
// forever.
const mockRouter = { refresh };
vi.mock('next/navigation', () => ({
  useRouter: () => mockRouter,
  usePathname: () => mockPathname,
}));

let mockPathname = '/';

let mockUser: User | null = null;
let mockAuthLoading = false;
vi.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: mockUser, isLoading: mockAuthLoading }),
}));

const listAddresses = vi.fn<() => Promise<Address[]>>(async () => []);
const deliveryArea = vi.fn<(lat: number, lng: number) => Promise<DeliveryArea>>(async () => ({
  serviceable: true,
  zone_name: 'Sharjah Central',
  delivery_fee: 0,
  free_threshold: 0,
  free_delivery_available: true,
  speed: 'express' as const,
  branch_id: 'sharjah',
}));
vi.mock('@/lib/api', () => ({
  addressesApi: { list: () => listAddresses() },
  deliveryApi: { area: (lat: number, lng: number) => deliveryArea(lat, lng) },
}));

let mockGuestAddresses: Address[] = [];
vi.mock('@/lib/guest-addresses', () => ({
  guestAddresses: { list: () => mockGuestAddresses },
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

const SHARJAH = { latitude: 25.3304139, longitude: 55.3736131 };
const ABU_DHABI = { latitude: 24.4539, longitude: 54.3773 };
const DUBAI_MARINA = { latitude: 25.0805, longitude: 55.1403 };

function address(over: Partial<Address> = {}): Address {
  return {
    id: 'a1',
    user_id: 'u1',
    label: 'Home',
    first_name: 'A',
    last_name: 'B',
    phone: '+971500000000',
    address_line_1: 'Somewhere',
    address_line_2: null,
    unit_number: null,
    country: 'AE',
    latitude: ABU_DHABI.latitude,
    longitude: ABU_DHABI.longitude,
    is_default: true,
    ...over,
  } as Address;
}

/** Installs a geolocation stack that reports `granted` and returns `fix`. */
function grantGeolocation(fix: { latitude: number; longitude: number }) {
  const getCurrentPosition = vi.fn(
    (success: PositionCallback) =>
      success({ coords: { ...fix } } as unknown as GeolocationPosition),
  );
  Object.defineProperty(navigator, 'geolocation', {
    value: { getCurrentPosition },
    configurable: true,
  });
  Object.defineProperty(navigator, 'permissions', {
    value: { query: async () => ({ state: 'granted' }) },
    configurable: true,
  });
  return getCurrentPosition;
}

/** A browser that has never been asked and would have to prompt. */
function withholdGeolocation() {
  const getCurrentPosition = vi.fn(
    (success: PositionCallback) =>
      success({ coords: { ...DUBAI_MARINA } } as unknown as GeolocationPosition),
  );
  Object.defineProperty(navigator, 'geolocation', {
    value: { getCurrentPosition },
    configurable: true,
  });
  Object.defineProperty(navigator, 'permissions', {
    value: { query: async () => ({ state: 'prompt' }) },
    configurable: true,
  });
  return getCurrentPosition;
}

/**
 * A real `localStorage`.
 *
 * The test environment exposes one whose methods are absent — Node's own
 * file-backed stub, started without a file — so the provider's every read and
 * write would take the `catch` branch and nothing under test would run.
 */
const store = new Map<string, string>();
Object.defineProperty(window, 'localStorage', {
  value: {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  },
  configurable: true,
});

/**
 * Renders the pin rather than assigning it to a variable the test can read:
 * writing to an outer binding during render is a side effect, and the React
 * Compiler lint rejects it.
 */
function Probe() {
  return <span data-testid="pin">{JSON.stringify(useLocation().location)}</span>;
}

/** Whatever the provider is currently handing its children. */
function seen(): Location {
  return JSON.parse(screen.getByTestId('pin').textContent ?? '{}') as Location;
}

function renderProvider() {
  return render(
    <LocationProvider>
      <Probe />
    </LocationProvider>,
  );
}

async function settled() {
  await waitFor(() => expect(screen.getByTestId('pin')).toBeInTheDocument());
  // Let the resolution chain's awaits drain.
  await act(async () => { await Promise.resolve(); });
}

function stored(): Location | null {
  const raw = window.localStorage.getItem(LOCATION_STORAGE_KEY);
  return raw ? (JSON.parse(raw) as Location) : null;
}

beforeEach(() => {
  store.clear();
  mockPathname = '/';
  mockUser = null;
  mockAuthLoading = false;
  mockGuestAddresses = [];
  listAddresses.mockReset();
  listAddresses.mockResolvedValue([]);
  refresh.mockClear();
  deliveryArea.mockClear();
});

// ─── Signing in and out ───────────────────────────────────────────────────────

describe('re-resolving when the account changes', () => {
  it("promotes the account's default address on sign-in", async () => {
    withholdGeolocation();
    window.localStorage.setItem(
      LOCATION_STORAGE_KEY,
      JSON.stringify({ ...DUBAI_MARINA, source: 'geolocation', label: null }),
    );

    const { rerender } = renderProvider();
    await settled();
    expect(seen().source).toBe('geolocation');

    listAddresses.mockResolvedValue([address()]);
    mockUser = { id: 'u1' } as User;
    await act(async () => {
      rerender(
        <LocationProvider>
          <Probe />
        </LocationProvider>,
      );
    });

    await waitFor(() => expect(seen().source).toBe('address'));
    expect(seen().latitude).toBeCloseTo(ABU_DHABI.latitude, 4);
  });

  it("drops the previous customer's address on sign-out", async () => {
    withholdGeolocation();
    listAddresses.mockResolvedValue([address()]);
    mockUser = { id: 'u1' } as User;

    const { rerender } = renderProvider();
    await settled();
    await waitFor(() => expect(seen().source).toBe('address'));

    mockUser = null;
    await act(async () => {
      rerender(
        <LocationProvider>
          <Probe />
        </LocationProvider>,
      );
    });

    await waitFor(() => expect(seen().source).toBe('default'));
    expect(stored()).toBeNull();
  });

  it("keeps the device's own reading across a sign-out", async () => {
    withholdGeolocation();
    window.localStorage.setItem(
      LOCATION_STORAGE_KEY,
      JSON.stringify({ ...DUBAI_MARINA, source: 'geolocation', label: null }),
    );
    mockUser = { id: 'u1' } as User;
    listAddresses.mockResolvedValue([]);

    const { rerender } = renderProvider();
    await settled();

    mockUser = null;
    await act(async () => {
      rerender(
        <LocationProvider>
          <Probe />
        </LocationProvider>,
      );
    });

    await act(async () => { await Promise.resolve(); });
    expect(seen().source).toBe('geolocation');
    expect(seen().latitude).toBeCloseTo(DUBAI_MARINA.latitude, 4);
  });

  it('resolves nothing until the session has been restored', async () => {
    withholdGeolocation();
    mockAuthLoading = true;
    listAddresses.mockResolvedValue([address()]);

    const { rerender } = renderProvider();
    await settled();
    expect(listAddresses).not.toHaveBeenCalled();

    mockAuthLoading = false;
    mockUser = { id: 'u1' } as User;
    await act(async () => {
      rerender(
        <LocationProvider>
          <Probe />
        </LocationProvider>,
      );
    });
    await waitFor(() => expect(seen().source).toBe('address'));
  });
});

// ─── The pin no longer freezes ────────────────────────────────────────────────

describe('refreshing a browser reading', () => {
  it('takes a fresh fix on load when permission is already granted', async () => {
    grantGeolocation(DUBAI_MARINA);
    window.localStorage.setItem(LOCATION_ASKED_KEY, '1');
    window.localStorage.setItem(
      LOCATION_STORAGE_KEY,
      JSON.stringify({ ...ABU_DHABI, source: 'geolocation', label: null }),
    );

    renderProvider();
    await settled();

    await waitFor(() => expect(seen().latitude).toBeCloseTo(DUBAI_MARINA.latitude, 4));
    expect(seen().source).toBe('geolocation');
    expect(stored()?.latitude).toBeCloseTo(DUBAI_MARINA.latitude, 4);
  });

  it('leaves a saved address alone however far the handset has wandered', async () => {
    grantGeolocation(DUBAI_MARINA);
    window.localStorage.setItem(LOCATION_ASKED_KEY, '1');
    listAddresses.mockResolvedValue([address()]);
    mockUser = { id: 'u1' } as User;

    renderProvider();
    await settled();
    await waitFor(() => expect(seen().source).toBe('address'));

    await act(async () => { await Promise.resolve(); });
    expect(seen().latitude).toBeCloseTo(ABU_DHABI.latitude, 4);
  });

  it('never prompts a browser that has not granted permission', async () => {
    const getCurrentPosition = withholdGeolocation();
    window.localStorage.setItem(LOCATION_ASKED_KEY, '1');
    window.localStorage.setItem(
      LOCATION_STORAGE_KEY,
      JSON.stringify({ ...ABU_DHABI, source: 'geolocation', label: null }),
    );

    renderProvider();
    await settled();

    expect(getCurrentPosition).not.toHaveBeenCalled();
    expect(seen().latitude).toBeCloseTo(ABU_DHABI.latitude, 4);
  });

  it('re-reads on a new screen, and only redraws when they have moved', async () => {
    const getCurrentPosition = grantGeolocation(SHARJAH);
    window.localStorage.setItem(LOCATION_ASKED_KEY, '1');
    window.localStorage.setItem(
      LOCATION_STORAGE_KEY,
      JSON.stringify({ ...SHARJAH, source: 'geolocation', label: null }),
    );

    const { rerender } = renderProvider();
    await settled();

    const lookupsAfterLoad = deliveryArea.mock.calls.length;
    const fixesAfterLoad = getCurrentPosition.mock.calls.length;

    mockPathname = '/cakes';
    await act(async () => {
      rerender(
        <LocationProvider>
          <Probe />
        </LocationProvider>,
      );
    });
    await act(async () => { await Promise.resolve(); });

    // It asked again — but the answer was the same street, so nothing moved
    // and no delivery lookup was spent on it.
    expect(getCurrentPosition.mock.calls.length).toBeGreaterThan(fixesAfterLoad);
    expect(deliveryArea.mock.calls.length).toBe(lookupsAfterLoad);
  });
});
