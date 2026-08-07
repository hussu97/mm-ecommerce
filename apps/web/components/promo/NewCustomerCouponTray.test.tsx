import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  featured: vi.fn(),
  locale: 'en',
}));

vi.mock('@/lib/api', () => ({
  promoApi: { featured: mocks.featured },
}));

// The real strings, so a test that passes is a test against copy a customer
// would actually read — including the parameters, which is the whole point of
// the tray reading its numbers off the API.
const STRINGS: Record<string, string> = {
  'promo.new_customer_title': '{percent}% off your first {orders} orders',
  'promo.new_customer_cta': 'Use code {code}',
  'promo.new_customer_apply': 'Apply',
  'promo.new_customer_applied': 'Applied',
  'promo.terms_title': 'Terms',
  'promo.terms_max': 'Up to {max} AED off per order.',
  'promo.terms_first_orders': 'Valid on your first {orders} orders.',
  'promo.terms_verify': 'Requires a verified mobile number.',
  'promo.terms_one_account': 'One offer per customer.',
  'promo.terms_limited': 'Limited time promotion.',
  'promo.terms_combine': 'Cannot be combined with other offers.',
};

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: mocks.locale,
    t: (key: string, params?: Record<string, string | number>) => {
      let value = STRINGS[key] ?? key;
      for (const [k, v] of Object.entries(params ?? {})) {
        value = value.replace(`{${k}}`, String(v));
      }
      return value;
    },
  }),
}));

import { NewCustomerCouponTray } from './NewCustomerCouponTray';

const COUPON = {
  code: 'FIRST20',
  code_ar: 'اول-20',
  discount_type: 'percentage' as const,
  discount_value: 20,
  max_discount_amount: 40,
  first_orders_limit: 2,
  requires_phone_verification: false,
};

function termsText(): string {
  return screen.getByRole('list').textContent ?? '';
}

describe('NewCustomerCouponTray', () => {
  beforeEach(() => {
    mocks.featured.mockReset();
    mocks.locale = 'en';
  });

  it('renders nothing when no campaign is running', async () => {
    mocks.featured.mockResolvedValue(null);
    const { container } = render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);

    await waitFor(() => expect(mocks.featured).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when the coupon cannot be fetched', async () => {
    mocks.featured.mockRejectedValue(new Error('offline'));
    const { container } = render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);

    await waitFor(() => expect(mocks.featured).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('quotes the API row rather than any figure of its own', async () => {
    mocks.featured.mockResolvedValue(COUPON);
    render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);

    expect(await screen.findByText('20% off your first 2 orders')).toBeInTheDocument();
    expect(screen.getByText('Use code FIRST20')).toBeInTheDocument();
    expect(termsText()).toContain('Up to 40 AED off per order.');
    expect(termsText()).toContain('Valid on your first 2 orders.');
  });

  it('follows the coupon when its configuration changes', async () => {
    mocks.featured.mockResolvedValue({
      ...COUPON,
      code: 'WELCOME10',
      discount_value: 10,
      max_discount_amount: 25,
      first_orders_limit: 3,
    });
    render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);

    expect(await screen.findByText('10% off your first 3 orders')).toBeInTheDocument();
    expect(screen.getByText('Use code WELCOME10')).toBeInTheDocument();
    expect(termsText()).toContain('Up to 25 AED off per order.');
  });

  it('never explains how a new customer is recognised', async () => {
    mocks.featured.mockResolvedValue(COUPON);
    render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);
    await screen.findByText('20% off your first 2 orders');

    // How eligibility is worked out is ours to know: it names identifiers the
    // customer never handed over for that purpose, and written down it reads as
    // instructions for getting around it.
    expect(termsText()).not.toMatch(/email|account|previous order|same (phone|number|device)/i);
    // But that it will not last forever is theirs to know.
    expect(termsText()).toContain('Limited time promotion.');
  });

  it('mentions verification only for a coupon that requires it', async () => {
    mocks.featured.mockResolvedValue(COUPON);
    const { unmount } = render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);
    await screen.findByText('20% off your first 2 orders');
    expect(termsText()).not.toContain('Requires a verified mobile number.');
    unmount();

    mocks.featured.mockResolvedValue({ ...COUPON, requires_phone_verification: true });
    render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);
    await screen.findByText('20% off your first 2 orders');
    expect(termsText()).toContain('Requires a verified mobile number.');
  });

  it('applies the code on one tap, without it being typed', async () => {
    mocks.featured.mockResolvedValue(COUPON);
    const onApply = vi.fn().mockResolvedValue(null);
    render(<NewCustomerCouponTray appliedCode={null} onApply={onApply} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));

    expect(onApply).toHaveBeenCalledWith('FIRST20');
  });

  it('offers the Arabic spelling of the code to an Arabic reader', async () => {
    mocks.locale = 'ar';
    mocks.featured.mockResolvedValue(COUPON);
    const onApply = vi.fn().mockResolvedValue(null);
    render(<NewCustomerCouponTray appliedCode={null} onApply={onApply} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));

    expect(onApply).toHaveBeenCalledWith('اول-20');
  });

  it('shows why it would not go on', async () => {
    mocks.featured.mockResolvedValue(COUPON);
    const onApply = vi.fn().mockResolvedValue('This code is for new customers only');
    render(<NewCustomerCouponTray appliedCode={null} onApply={onApply} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This code is for new customers only',
    );
  });

  it('reads as applied whichever spelling went on the basket', async () => {
    mocks.featured.mockResolvedValue(COUPON);
    render(<NewCustomerCouponTray appliedCode="اول-20" onApply={vi.fn()} />);

    expect(await screen.findByText('Applied')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
  });

  it('stays out of the way of a coupon it has no honest wording for', async () => {
    mocks.featured.mockResolvedValue({ ...COUPON, discount_type: 'fixed', discount_value: 20 });
    const { container } = render(<NewCustomerCouponTray appliedCode={null} onApply={vi.fn()} />);

    await waitFor(() => expect(mocks.featured).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
