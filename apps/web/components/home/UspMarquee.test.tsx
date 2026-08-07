import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { UspMarquee } from './UspMarquee';
import type { DeliveryArea } from '@/lib/location/types';

// ─── Mocks ────────────────────────────────────────────────────────────────────

const CATALOGUE: Record<string, string> = {
  'usp.speed_express': 'Delivered in about an hour',
  'usp.speed_same_day': 'Same-day delivery',
  'usp.speed_next_day': 'Next-day delivery',
};

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: 'en',
    direction: 'ltr' as const,
    translations: CATALOGUE,
    t: (key: string) => CATALOGUE[key] ?? key,
  }),
}));

let mockArea: DeliveryArea | null = null;
let mockIsKnown = true;

vi.mock('@/lib/location/LocationProvider', () => ({
  useLocation: () => ({
    location: { latitude: 0, longitude: 0, source: 'address' as const, label: null },
    area: mockArea,
    loading: false,
    isKnown: mockIsKnown,
    setLocation: vi.fn(),
    requestBrowserLocation: vi.fn(),
  }),
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function area(speed: DeliveryArea['speed']): DeliveryArea {
  return {
    serviceable: true,
    zone_name: 'Sharjah Central',
    delivery_fee: 0,
    free_threshold: 0,
    free_delivery_available: true,
    speed,
  };
}

const CMS = { items: [{ icon: 'cake', label: 'Baked to order' }, { label: 'Halal' }] };

/** Only the first run of the track is read out; the rest are decoration. */
function firstRun(container: HTMLElement): HTMLElement {
  return container.querySelector('.mm-marquee-run') as HTMLElement;
}

beforeEach(() => {
  mockArea = null;
  mockIsKnown = true;
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('UspMarquee', () => {
  it('leads with the delivery speed for the customer’s own area', () => {
    mockArea = area('express');
    const { container } = render(<UspMarquee c={CMS} />);
    const labels = within(firstRun(container))
      .getAllByText(/./, { selector: 'span.font-body' })
      .map(n => n.textContent);
    expect(labels).toEqual(['Delivered in about an hour', 'Baked to order', 'Halal']);
  });

  it.each([
    ['same_day', 'Same-day delivery'],
    ['next_day', 'Next-day delivery'],
  ] as const)('maps %s to its own promise', (speed, copy) => {
    mockArea = area(speed);
    render(<UspMarquee c={CMS} />);
    expect(screen.getAllByText(copy).length).toBeGreaterThan(0);
  });

  it('omits the speed item rather than guessing when the area is unknown', () => {
    mockArea = null;
    const { container } = render(<UspMarquee c={CMS} />);
    expect(firstRun(container).textContent).not.toMatch(/delivery|hour/i);
    expect(screen.getAllByText('Baked to order').length).toBeGreaterThan(0);
  });

  it('still shows the speed item when the location is the shop standing in', () => {
    // With nobody located the area lookup runs against the Sharjah kitchen, so
    // the answer is the shop's own zone. Shown rather than withheld: this is a
    // Sharjah bakery whose nearest customers are its largest group, and the
    // banner beside the strip names the area and offers to change it, so the
    // claim never stands unqualified.
    mockArea = area('express');
    mockIsKnown = false;
    const { container } = render(<UspMarquee c={CMS} />);
    expect(firstRun(container).textContent).toMatch(/hour/i);
  });

  it('still omits it when the lookup itself failed', () => {
    // The distinction that survives: an unlocated customer gets the shop's
    // default, but a lookup that returned nothing has no speed to report and
    // must not invent one.
    mockArea = null;
    mockIsKnown = false;
    const { container } = render(<UspMarquee c={CMS} />);
    expect(firstRun(container).textContent).not.toMatch(/hour/i);
  });

  it('renders nothing when there is neither a speed nor a CMS item', () => {
    mockArea = null;
    const { container } = render(<UspMarquee c={{}} />);
    expect(container.firstChild).toBeNull();
  });

  it('still renders when the speed is the only item', () => {
    mockArea = area('express');
    render(<UspMarquee c={{}} />);
    expect(screen.getAllByText('Delivered in about an hour').length).toBeGreaterThan(0);
  });
});
