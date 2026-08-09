/**
 * The offer, said before anybody has decided to buy.
 *
 * Until this strip existed the coupon lived only in the basket tray, which
 * meant an acquisition offer was claimed almost entirely by people already
 * buying. The risk of moving it earlier is the opposite one: a strip that keeps
 * advertising a campaign that has ended, or one that quotes a figure the
 * checkout will not honour. Everything below is about that — the bar states
 * what the row says or it does not appear.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  shown: vi.fn(),
  clicked: vi.fn(),
  locale: 'en',
}));

vi.mock('@/lib/analytics', () => ({
  analytics: { couponBannerShown: mocks.shown, couponBannerClicked: mocks.clicked },
}));

const STRINGS: Record<string, string> = {
  'promo.new_customer_title': '{percent}% off your first {orders} orders',
  'promo.use_code': 'Use code',
  'promo.dismiss_banner': 'Dismiss offer',
};

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: mocks.locale,
    t: (key: string, params?: Record<string, string | number>) => {
      let value = STRINGS[key] ?? key;
      for (const [k, v] of Object.entries(params ?? {})) value = value.replace(`{${k}}`, String(v));
      return value;
    },
  }),
}));

import { PromoBanner } from './PromoBanner';

const COUPON = {
  code: 'NEW',
  code_ar: 'جديد',
  discount_type: 'percentage' as const,
  discount_value: 20,
  max_discount_amount: 30,
  first_orders_limit: 3,
  requires_phone_verification: true,
};

describe('PromoBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.locale = 'en';
    sessionStorage.clear();
  });

  it('quotes the row rather than any figure of its own', () => {
    render(<PromoBanner promo={COUPON} />);

    expect(screen.getByText(/20% off your first 3 orders/)).toBeInTheDocument();
    expect(screen.getByText('Use code')).toBeInTheDocument();
    // Boxed and set apart, not run together with the sentence explaining the
    // offer — it is the one part of the strip the customer has to type.
    expect(screen.getByText('NEW')).toBeInTheDocument();
  });

  it('renders nothing when no campaign is running', () => {
    const { container } = render(<PromoBanner promo={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('stays out of the way of a coupon it has no honest wording for', () => {
    // The copy is percentage-shaped, so a 20-dirham coupon rendered through it
    // would read as "20% off" — a figure the checkout would then refuse.
    const { container } = render(
      <PromoBanner promo={{ ...COUPON, discount_type: 'fixed', discount_value: 20 }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('offers the Arabic spelling of the code to an Arabic reader', () => {
    mocks.locale = 'ar';
    render(<PromoBanner promo={COUPON} />);

    expect(screen.getByText('جديد')).toBeInTheDocument();
  });

  it('stays dismissed for the rest of the session', () => {
    const { container, unmount } = render(<PromoBanner promo={COUPON} />);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss offer' }));
    expect(container).toBeEmptyDOMElement();

    unmount();
    const second = render(<PromoBanner promo={COUPON} />);
    expect(second.container).toBeEmptyDOMElement();
  });

  it('counts the impression, so the click has a denominator', () => {
    render(<PromoBanner promo={COUPON} />);
    expect(mocks.shown).toHaveBeenCalledWith({ code: 'NEW', percent: 20 });

    fireEvent.click(screen.getByRole('link'));
    expect(mocks.clicked).toHaveBeenCalledWith({ code: 'NEW' });
  });

  it('does not count an impression nobody had', () => {
    sessionStorage.setItem('mm_promo_banner_dismissed', '1');
    render(<PromoBanner promo={COUPON} />);
    expect(mocks.shown).not.toHaveBeenCalled();
  });
});
