import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DeliveryPromiseBanner } from './DeliveryPromiseBanner';
import type { DeliveryArea } from '@/lib/location/types';

// ─── Mocks ────────────────────────────────────────────────────────────────────

//: The real catalogue lives in the database; these are the exact English
//: strings seeded for the keys this banner uses, so interpolation is tested
//: against the real shape of the copy rather than a stand-in.
const CATALOGUE: Record<string, string> = {
  'usp.free_delivery_here': 'Free delivery in {area}',
  'usp.free_delivery_over': 'Free delivery over {amount} AED in {area}',
  'usp.delivering_to': 'Delivering to {area}',
  'usp.change_area': 'Change',
  'promo_banner.text': 'Free delivery over 150 AED in selected areas',
};

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: 'en',
    direction: 'ltr' as const,
    translations: CATALOGUE,
    t: (key: string, params?: Record<string, string | number>) => {
      let value = CATALOGUE[key] ?? key;
      for (const [k, v] of Object.entries(params ?? {})) {
        value = value.replace(`{${k}}`, String(v));
      }
      return value;
    },
  }),
}));

const requestBrowserLocation = vi.fn(async () => true);
let mockArea: DeliveryArea | null = null;
let mockIsKnown = false;

vi.mock('@/lib/location/LocationProvider', () => ({
  useLocation: () => ({
    location: { latitude: 0, longitude: 0, source: 'default' as const, label: null },
    area: mockArea,
    loading: false,
    isKnown: mockIsKnown,
    setLocation: vi.fn(),
    requestBrowserLocation,
  }),
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function area(over: Partial<DeliveryArea> = {}): DeliveryArea {
  return {
    serviceable: true,
    zone_name: 'Sharjah Central',
    delivery_fee: 0,
    free_threshold: 0,
    free_delivery_available: true,
    speed: 'express',
    ...over,
  };
}

beforeEach(() => {
  mockArea = null;
  mockIsKnown = false;
  requestBrowserLocation.mockClear();
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('DeliveryPromiseBanner', () => {
  it('names the zone when delivery is free there with no minimum', () => {
    mockArea = area();
    mockIsKnown = true;
    render(<DeliveryPromiseBanner />);
    expect(screen.getByText('Free delivery in Sharjah Central')).toBeInTheDocument();
  });

  it('states the threshold and the zone when free delivery has a minimum', () => {
    mockArea = area({ zone_name: 'Dubai Near', free_threshold: 150, delivery_fee: 25 });
    mockIsKnown = true;
    render(<DeliveryPromiseBanner />);
    expect(screen.getByText('Free delivery over 150 AED in Dubai Near')).toBeInTheDocument();
  });

  it('falls back to naming the place when there is no free delivery to promise', () => {
    mockArea = area({
      zone_name: 'Ras al-Khaimah',
      free_delivery_available: false,
      free_threshold: null,
      delivery_fee: null,
    });
    mockIsKnown = true;
    render(<DeliveryPromiseBanner />);
    expect(screen.getByText('Delivering to Ras al-Khaimah')).toBeInTheDocument();
  });

  it('says nothing specific while the lookup has not landed', () => {
    mockArea = null;
    mockIsKnown = true;
    render(<DeliveryPromiseBanner />);
    expect(
      screen.getByText('Free delivery over 150 AED in selected areas'),
    ).toBeInTheDocument();
  });

  it('makes no promise about an address it cannot reach', () => {
    mockArea = area({ serviceable: false, zone_name: null, free_delivery_available: false });
    mockIsKnown = true;
    render(<DeliveryPromiseBanner />);
    expect(
      screen.getByText('Free delivery over 150 AED in selected areas'),
    ).toBeInTheDocument();
  });

  it('offers to use the browser location while the location is a stand-in', () => {
    mockArea = area();
    mockIsKnown = false;
    render(<DeliveryPromiseBanner />);

    fireEvent.click(screen.getByRole('button', { name: /change/i }));
    expect(requestBrowserLocation).toHaveBeenCalledOnce();
  });

  it('hides the location affordance once the location is real', () => {
    mockArea = area();
    mockIsKnown = true;
    render(<DeliveryPromiseBanner />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('bounds the promise to a named zone rather than stating it flatly', () => {
    // The unlocated case: the pin is the shop, so the only honest version of
    // "free delivery" is one that says where.
    mockArea = area();
    mockIsKnown = false;
    render(<DeliveryPromiseBanner />);
    expect(screen.getByText('Free delivery in Sharjah Central')).toBeInTheDocument();
  });
});
