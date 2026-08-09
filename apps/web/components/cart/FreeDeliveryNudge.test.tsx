import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FreeDeliveryNudge } from './FreeDeliveryNudge';
import type { DeliveryArea } from '@/lib/location/types';

// ─── Mocks ────────────────────────────────────────────────────────────────────

//: The exact English strings seeded for the keys this component uses, so
//: interpolation is exercised against the real copy rather than a stand-in.
const CATALOGUE: Record<string, string> = {
  'cart.free_delivery_here': 'Delivery is free in {area}.',
  'cart.free_delivery_qualified': "You've earned free delivery in {area}.",
  'cart.free_delivery_remaining': 'Add {amount} AED more for free delivery in {area}.',
  'cart.free_delivery_progress_label': 'Progress towards free delivery',
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

let mockArea: DeliveryArea | null = null;

vi.mock('@/lib/location/LocationProvider', () => ({
  useLocation: () => ({
    location: { latitude: 0, longitude: 0, source: 'default' as const, label: null },
    area: mockArea,
    loading: false,
    isKnown: false,
    setLocation: vi.fn(),
    requestBrowserLocation: vi.fn(),
  }),
}));

const unlocked = vi.fn();
vi.mock('@/lib/analytics', () => ({
  analytics: { freeDeliveryUnlocked: (...args: unknown[]) => unlocked(...args) },
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function area(over: Partial<DeliveryArea> = {}): DeliveryArea {
  return {
    serviceable: true,
    zone_name: 'Sharjah Central',
    delivery_fee: 0,
    free_threshold: 100,
    free_delivery_available: true,
    speed: 'express',
    ...over,
  };
}

beforeEach(() => {
  mockArea = area();
  unlocked.mockClear();
});

// ─── Silence, where a promise cannot be bounded to a place ────────────────────

describe('when there is no zone to name', () => {
  /**
   * The rule inherited from `DeliveryPromiseBanner`, and the reason it exists:
   * there is no national threshold that is true. Sharjah is free at any basket
   * and the far emirates want 200, so a generic "spend 50 more" is wrong almost
   * everywhere it would be read.
   */
  it('says nothing before the area lookup lands', () => {
    mockArea = null;
    const { container } = render(<FreeDeliveryNudge total={20} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says nothing for an address we do not deliver to', () => {
    mockArea = area({ serviceable: false });
    const { container } = render(<FreeDeliveryNudge total={20} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says nothing when the zone is serviceable but unnamed', () => {
    mockArea = area({ zone_name: null });
    const { container } = render(<FreeDeliveryNudge total={20} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says nothing where free delivery is not on offer at all', () => {
    mockArea = area({ free_delivery_available: false });
    const { container } = render(<FreeDeliveryNudge total={20} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says nothing when there is no threshold to measure against', () => {
    mockArea = area({ free_threshold: null });
    const { container } = render(<FreeDeliveryNudge total={20} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// ─── What it says when it can ─────────────────────────────────────────────────

describe('the nudge', () => {
  it('names the remaining amount and the zone', () => {
    render(<FreeDeliveryNudge total={82} />);
    expect(
      screen.getByText('Add 18 AED more for free delivery in Sharjah Central.'),
    ).toBeInTheDocument();
  });

  /**
   * A whole number loses its `.00`; a fractional one keeps two places rather
   * than being rounded to something the checkout would then disagree with.
   * Same helper and same behaviour as `DeliveryPromiseBanner`.
   */
  it('drops a trailing .00 but keeps a real fraction', () => {
    render(<FreeDeliveryNudge total={87.5} />);
    expect(screen.getByText(/Add 12\.50 AED more/)).toBeInTheDocument();
  });

  it('shows progress towards the threshold', () => {
    render(<FreeDeliveryNudge total={25} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
  });

  it('congratulates once the basket is over the line', () => {
    render(<FreeDeliveryNudge total={120} />);
    expect(
      screen.getByText("You've earned free delivery in Sharjah Central."),
    ).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('counts the exact threshold as earned', () => {
    render(<FreeDeliveryNudge total={100} />);
    expect(screen.getByText(/You've earned free delivery/)).toBeInTheDocument();
  });

  /**
   * Sharjah. A progress bar towards zero would imply the customer earned
   * something by spending, which is a small lie about a real zone.
   */
  it('states a zero threshold as a fact, with no bar', () => {
    mockArea = area({ free_threshold: 0 });
    render(<FreeDeliveryNudge total={0} />);
    expect(screen.getByText('Delivery is free in Sharjah Central.')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });
});

// ─── Analytics ────────────────────────────────────────────────────────────────

describe('the unlocked event', () => {
  it('fires once when the line is crossed, not on every render', () => {
    const { rerender } = render(<FreeDeliveryNudge total={120} />);
    rerender(<FreeDeliveryNudge total={130} />);
    rerender(<FreeDeliveryNudge total={140} />);
    expect(unlocked).toHaveBeenCalledTimes(1);
    expect(unlocked).toHaveBeenCalledWith({
      threshold: 100,
      subtotal: 120,
      surface: 'cart',
      zone: 'Sharjah Central',
    });
  });

  it('does not fire for a basket still short of the threshold', () => {
    render(<FreeDeliveryNudge total={99} />);
    expect(unlocked).not.toHaveBeenCalled();
  });

  /** Removing an item and re-adding it is a second unlock, and reads as one. */
  it('fires again after the basket drops back under and returns', () => {
    const { rerender } = render(<FreeDeliveryNudge total={120} />);
    rerender(<FreeDeliveryNudge total={40} />);
    rerender(<FreeDeliveryNudge total={120} />);
    expect(unlocked).toHaveBeenCalledTimes(2);
  });
});
