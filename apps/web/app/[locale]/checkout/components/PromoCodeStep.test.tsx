/**
 * A coupon has to be judged on the same facts the order will be.
 *
 * The server refuses a new-customer code on an account, an email *or* a phone
 * that has ordered before, and `create_order` checks all three. Validating on
 * the code and the subtotal alone therefore answers a different question from
 * the one the pay button is judged on: the discount reads as applied all the
 * way down the form, and the order is refused at the last step, where a
 * customer has nothing left to do but leave.
 *
 * The second half of the same problem is the condition that is not a refusal at
 * all. An unproved mobile number is something the customer can still do, and
 * only a delivery order is ever asked for it — so the discount applies, the
 * step is reported upward, and the SMS is asked for on the address form. This
 * panel deliberately no longer offers one: it is folded away behind an "add
 * promo or note" toggle most customers never open, which is the last place a
 * required step should hide, and a guest — precisely who this coupon is for —
 * had no other reachable way to prove anything.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  validate: vi.fn(),
  addToast: vi.fn(),
  verifiedWith: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  promoApi: { validate: mocks.validate },
}));

vi.mock('@/components/ui/Toast', () => ({
  useToast: () => ({ addToast: mocks.addToast }),
}));

vi.mock('@/lib/analytics', () => ({
  analytics: { promoApplied: vi.fn(), promoFailed: vi.fn() },
}));

// A build with the Firebase vars in it. Without them the component renders
// nothing, which is the preview-deploy case the step falls back to copy for.
vi.mock('@/lib/firebase', () => ({ isFirebaseConfigured: () => true }));

// Stood in for, because the real one loads the Firebase SDK. What matters here
// is that it is rendered with the customer's number and that confirming it
// re-applies the code — not how the SMS gets sent.
vi.mock('@/components/ui/PhoneVerify', () => ({
  PhoneVerify: ({ phone, onVerified }: { phone: string; onVerified: (p: string) => void }) => (
    <button type="button" onClick={() => { mocks.verifiedWith(phone); onVerified(phone); }}>
      send-code-to-{phone}
    </button>
  ),
}));

const STRINGS: Record<string, string> = {
  'checkout.promo_placeholder': 'Promo code',
  'checkout.apply': 'Apply',
  'checkout.invalid_promo': 'Invalid code',
  'checkout.promo_error': 'Something went wrong',
  'checkout.promo_applied': 'Applied {code}',
  'checkout.remove_promo': 'Remove',
  'verify.subtitle': "We'll text you a 6-digit code.",
  'verify.enter_phone_first': 'Add your mobile number above, then apply the code again.',
};

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: 'en',
    t: (key: string, params?: Record<string, string | number>) => {
      let value = STRINGS[key] ?? key;
      for (const [k, v] of Object.entries(params ?? {})) value = value.replace(`{${k}}`, String(v));
      return value;
    },
  }),
}));

import { PromoCodeStep } from './PromoCodeStep';

function renderStep(over: Partial<React.ComponentProps<typeof PromoCodeStep>> = {}) {
  const onChange = vi.fn();
  render(
    <PromoCodeStep
      promoCode="WELCOME15"
      promoDiscount={0}
      promoMessage=""
      subtotal={100}
      identity={{ email: 'someone@example.com', phone: '+971501234567' }}
      onChange={onChange}
      {...over}
    />,
  );
  return { onChange };
}

describe('PromoCodeStep', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.validate.mockResolvedValue({ valid: true, discount_amount: 15, message: null });
  });

  it('validates against the same identity the order will be written under', async () => {
    renderStep();
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() => expect(mocks.validate).toHaveBeenCalled());
    expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, {
      email: 'someone@example.com',
      phone: '+971501234567',
    });
  });

  it('applies the discount even while a number is still unproved', async () => {
    mocks.validate.mockResolvedValue({
      valid: true,
      discount_amount: 15,
      message: null,
      requires_phone_verification: true,
      phone_verified: false,
    });

    const { onChange } = renderStep();
    fireEvent.click(screen.getByText('Apply'));

    // The discount is real and goes on the form. Refusing it here is what used
    // to leave a new customer reading an advert for an offer they were told,
    // on the same screen, that they could not have.
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ promoCode: 'WELCOME15', promoDiscount: 15 }),
      ),
    );
  });

  it('reports an outstanding verification upward rather than handling it here', async () => {
    mocks.validate.mockResolvedValue({
      valid: true,
      discount_amount: 15,
      message: null,
      requires_phone_verification: true,
      phone_verified: false,
    });

    const { onChange } = renderStep();
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ promoNeedsVerify: true }),
      ),
    );

    // And no OTP panel here. This step is folded away behind an "add promo or
    // note" toggle that most customers never open — the last place a required
    // step should hide. Verification lives on the address form, where the
    // number is being typed anyway and where a guest can reach it.
    expect(screen.queryByText(/send-code-to/)).not.toBeInTheDocument();
  });

  it('does not claim a verification is owed when the number already proved one', async () => {
    mocks.validate.mockResolvedValue({
      valid: true,
      discount_amount: 15,
      message: null,
      requires_phone_verification: true,
      phone_verified: true,
    });

    const { onChange } = renderStep();
    fireEvent.click(screen.getByText('Apply'));

    // The coupon carries the gate; this customer has already cleared it. Asking
    // again would be a second SMS for a handset that proved itself once.
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ promoNeedsVerify: false }),
      ),
    );
  });

  it('tells the server which kind of order this is', async () => {
    renderStep({
      identity: { email: null, phone: '+971501234567', delivery_method: 'pickup' },
    });
    fireEvent.click(screen.getByText('Apply'));

    // The phone gate is a delivery rule — a collection costs us no courier and
    // is never asked. Validating without saying which this is gets the cautious
    // answer, and a pickup order is then told it owes a number it will never be
    // asked for.
    await waitFor(() =>
      expect(mocks.validate).toHaveBeenCalledWith(
        'WELCOME15',
        100,
        expect.objectContaining({ delivery_method: 'pickup' }),
      ),
    );
  });

  it('does not offer an OTP for a refusal an OTP cannot fix', async () => {
    mocks.validate.mockResolvedValue({
      valid: false,
      discount_amount: 0,
      message: 'This code is for new customers only',
    });

    renderStep();
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() =>
      expect(screen.getByText('This code is for new customers only')).toBeInTheDocument(),
    );
    expect(screen.queryByText(/send-code-to/)).not.toBeInTheDocument();
  });

  it('re-checks an applied code when the identity changes under it', async () => {
    // A customer applies the code, then types the phone number that turns out to
    // have ordered three times before. The discount on the summary has to stop
    // being real at that point, not at the pay button.
    const { rerender } = render(
      <PromoCodeStep
        promoCode="WELCOME15"
        promoDiscount={15}
        promoMessage=""
        subtotal={100}
        identity={{ email: null, phone: null }}
        onChange={vi.fn()}
      />,
    );

    mocks.validate.mockResolvedValue({
      valid: false,
      discount_amount: 0,
      message: 'This code is for new customers only',
    });

    rerender(
      <PromoCodeStep
        promoCode="WELCOME15"
        promoDiscount={15}
        promoMessage=""
        subtotal={100}
        identity={{ email: null, phone: '+971509999999' }}
        onChange={vi.fn()}
      />,
    );

    await waitFor(
      () =>
        expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, {
          email: null,
          phone: '+971509999999',
        }),
      { timeout: 2000 },
    );
  });

  it('never talks to the server when nothing is applied and nothing is typed', async () => {
    render(
      <PromoCodeStep
        promoCode=""
        promoDiscount={0}
        promoMessage=""
        subtotal={100}
        identity={{ email: 'a@b.com', phone: null }}
        onChange={vi.fn()}
      />,
    );
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect(mocks.validate).not.toHaveBeenCalled();
  });
});
